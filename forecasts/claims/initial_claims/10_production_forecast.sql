-- Production SA initial-claims forecast.
--
-- Champion: pure TimesFM 2.5 (`AI.FORECAST`, zero-shot, 2023-01-01 context
-- floor), horizons 1..13. Promoted 2026-10-26, replacing "ens_w60"
-- (0.6*ARIMA_PLUS + 0.4*seasonal-naive), which is demoted to a diagnostic.
--
-- Why: over 119 walk-forward origins (2024-07-06 .. 2026-10-10) scored on
-- first-print SA, TimesFM 2.5 beats the random walk at every horizon 1..13 and
-- beats TimesFM 3.0 at every horizon too; live, ens_w60 had decayed to a
-- +15.7k pure bias at h=1 (every error positive for six months). Per-horizon
-- MAE from that backtest:
--
--     h      1      2      3      4      5      6      7
--   tfm25  6,629  7,685  8,460  8,648  8,983  9,068  9,258
--   tfm3   6,802  7,886  8,675  8,855  9,366  9,590  9,971
--   rw     7,529  9,347 10,752 10,750 11,139 11,763 11,292
--
--     h      8      9     10     11     12     13    mean
--   tfm25  9,471  9,737  9,926 10,163 10,396 10,479  9,146
--   tfm3  10,217 10,534 10,840 11,178 11,612 11,785  9,793
--   rw    11,080 10,865 11,600 11,202 11,593 11,393 10,793
--
-- The live scorecard showed RW beating TimesFM at h>=5. That is one regime,
-- not a result: restricted to origins from 2026-04-18 it holds (RW 13,364 vs
-- 14,714) and on the earlier 837 observations it reverses hard (10,930 vs
-- 8,753). A horizon-dependent TimesFM/RW blend would be fitted to ~160
-- observations of a single down-trending stretch, so there is no blend here.
--
-- ens_w60 is still computed and stored as its components (arima_sa, snaive_sa)
-- so the demotion stays auditable: 0.6*arima_sa + 0.4*snaive_sa reconstructs
-- the old primary exactly, and both legs keep scoring against the new one.
--
-- The SA series is fct_sa_input (DOLETA, with the DOL press advance for the
-- recent weeks DOLETA's XML lags on) so the origin tracks the latest release
-- rather than DOLETA's stale last week.
--
-- Run order: 01_views_pit_actuals.sql (the retained PIT/actuals layer) then
-- this file. Project/dataset hardcoded to us-econ-51920.claims.

-- ---------------------------------------------------------------------------
-- Output: append-only (matches the repo's append-only collector philosophy).
-- Each run adds one generation; the _current view exposes only the newest, so
-- the coming week is always the latest-data forecast while history is kept for
-- later realized-accuracy checks.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `us-econ-51920.claims.forecast_sa_initial_claims` (
  generated_at  TIMESTAMP NOT NULL,           -- when this forecast was produced
  data_through  DATE      NOT NULL,           -- last actual SA week it used
  horizon       INT64     NOT NULL,           -- weeks ahead (1..13)
  target_week   DATE      NOT NULL,           -- week being forecast
  sa_forecast   FLOAT64,                      -- the champion; see model_version
  arima_sa      FLOAT64,                      -- diagnostic / ens_w60 component
  snaive_sa     FLOAT64,                      -- diagnostic / ens_w60 component
  timesfm_sa    FLOAT64,                      -- pure TimesFM 2.5
  model_version STRING                        -- which model sa_forecast holds
) PARTITION BY data_through;

-- ---------------------------------------------------------------------------
-- Migration for the 2026-10-26 champion change. Idempotent -- safe to re-run,
-- and a no-op on a fresh environment created by the DDL above.
--
-- `sa_forecast` changes meaning at this generation, so every row has to say
-- which model produced it. Without the stamp, anyone computing MAE over the
-- column's full history silently averages two different models.
-- ---------------------------------------------------------------------------
ALTER TABLE `us-econ-51920.claims.forecast_sa_initial_claims`
  ADD COLUMN IF NOT EXISTS model_version STRING;

UPDATE `us-econ-51920.claims.forecast_sa_initial_claims`
SET model_version = 'ens_w60'
WHERE model_version IS NULL;


-- _current surfaces the newest generation. sa_forecast is TimesFM 2.5 from
-- 2026-10-26 on (model_version = 'timesfm25_v1'); timesfm_sa carries the same
-- number by construction and is kept so the column's history stays continuous.
CREATE OR REPLACE VIEW `us-econ-51920.claims.forecast_sa_initial_claims_current` AS
SELECT *
FROM `us-econ-51920.claims.forecast_sa_initial_claims`
WHERE generated_at = (
  SELECT MAX(generated_at) FROM `us-econ-51920.claims.forecast_sa_initial_claims`
);

-- ---------------------------------------------------------------------------
-- The forecast procedure. Trains on fct_sa_input each run (latest-vintage SA
-- per week, DOLETA preferred with press-advance fallback), so the origin and
-- the coming week always reflect the latest release. Errors are intentionally
-- NOT swallowed -- a scheduled run that fails should surface, not silently skip.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE `us-econ-51920.claims.fct_forecast_sa_initial_claims`()
BEGIN
  DECLARE gen_ts    TIMESTAMP DEFAULT CURRENT_TIMESTAMP();
  DECLARE today     DATE      DEFAULT CURRENT_DATE();
  DECLARE model_ver STRING    DEFAULT 'timesfm25_v1';
  DECLARE origin    DATE;
  DECLARE tf_rows   INT64;
  DECLARE opts      STRING    DEFAULT "model_type='ARIMA_PLUS', time_series_timestamp_col='week_ending', time_series_data_col='value', data_frequency='WEEKLY', holiday_region='US'";

  -- Freshest SA week we have (DOLETA, or press advance for the recent weeks
  -- DOLETA's XML lags on) = the forecast origin.
  EXECUTE IMMEDIATE FORMAT("""
    SELECT MAX(week_ending)
    FROM `us-econ-51920.claims.fct_sa_input`
    WHERE week_ending <= DATE '%t'
  """, today) INTO origin;

  -- Champion: pure TimesFM 2.5 over the 2023-01-01 floor history (single
  -- series; AI.FORECAST is zero-shot, so there is no model object to train).
  CREATE TEMP TABLE tf_input AS
  SELECT TIMESTAMP(week_ending) AS ts, value AS sa
  FROM `us-econ-51920.claims.fct_sa_input`
  WHERE week_ending BETWEEN DATE '2023-01-01' AND origin;

  CREATE TEMP TABLE tf_fc AS
  SELECT DATE(forecast_timestamp) AS target_week,
         forecast_value AS timesfm_sa
  FROM AI.FORECAST(TABLE tf_input,
         data_col => 'sa', timestamp_col => 'ts',
         model => 'TimesFM 2.5', horizon => 13);

  -- The champion is now the TimesFM column, so a short or null AI.FORECAST
  -- result must fail the run rather than publish NULL into sa_forecast. (Under
  -- ens_w60 this was a LEFT JOIN and a missing TimesFM row was cosmetic.)
  SET tf_rows = (SELECT COUNT(*) FROM tf_fc WHERE timesfm_sa IS NOT NULL);
  IF tf_rows <> 13 THEN
    RAISE USING MESSAGE = FORMAT(
      'AI.FORECAST returned %d usable rows, expected 13 -- refusing to publish',
      tf_rows);
  END IF;

  -- ARIMA_PLUS on the same series: no longer the primary, kept as a diagnostic
  -- and as the ens_w60 leg so the demoted blend stays reconstructible.
  EXECUTE IMMEDIATE FORMAT("""
    CREATE OR REPLACE MODEL `us-econ-51920.claims.fct_prod_sa_arima`
    OPTIONS(%s) AS
    SELECT week_ending, value
    FROM `us-econ-51920.claims.fct_sa_input`
    WHERE week_ending BETWEEN DATE '2023-01-01' AND DATE '%t'
  """, opts, origin);

  -- One row per horizon: the champion plus the diagnostic legs.
  -- Wrapped in EXECUTE IMMEDIATE on purpose: the ML.FORECAST(MODEL ...) below
  -- references fct_prod_sa_arima, which the procedure creates dynamically just
  -- above. A plain reference here would be validated at CREATE PROCEDURE time
  -- (before the model exists) and the proc would refuse to be created.
  EXECUTE IMMEDIATE FORMAT("""
    INSERT INTO `us-econ-51920.claims.forecast_sa_initial_claims`
      (generated_at, data_through, horizon, target_week,
       sa_forecast, arima_sa, snaive_sa, timesfm_sa, model_version)
    WITH arima AS (
      SELECT DATE(forecast_timestamp) AS target_week,
             DATE_DIFF(DATE(forecast_timestamp), DATE '%t', WEEK) AS horizon,
             forecast_value AS arima_sa
      FROM ML.FORECAST(MODEL `us-econ-51920.claims.fct_prod_sa_arima`,
                       STRUCT(19 AS horizon, 0.9 AS confidence_level))
      WHERE DATE(forecast_timestamp) > DATE '%t'
        AND DATE_DIFF(DATE(forecast_timestamp), DATE '%t', WEEK) BETWEEN 1 AND 13
    ),
    sn AS (
      SELECT week_ending, value
      FROM `us-econ-51920.claims.fct_sa_input`
      WHERE week_ending <= DATE '%t'
    )
    SELECT TIMESTAMP '%t', DATE '%t', a.horizon, a.target_week,
           tf.timesfm_sa,
           a.arima_sa, sn.value, tf.timesfm_sa,
           '%s'
    FROM arima a
    JOIN sn ON sn.week_ending = DATE_SUB(a.target_week, INTERVAL 364 DAY)
    JOIN tf_fc tf ON tf.target_week = a.target_week
  """, origin, origin, origin, origin, gen_ts, origin, model_ver);
END;

-- ---------------------------------------------------------------------------
-- Run it now:
--   CALL `us-econ-51920.claims.fct_forecast_sa_initial_claims`();
--   SELECT * FROM `us-econ-51920.claims.forecast_sa_initial_claims_current`
--   ORDER BY horizon;
--
-- Keep it current: the BigQuery Scheduled Query `claims_ens_arima_forecast`
-- (every thu 17:00 UTC, created by hand in the console 2026-05-20, NOT under
-- Terraform) calls the procedure. Its body is just the CALL above, so the
-- champion change needs no edit there -- but the name is now a misnomer.
-- Each run appends a fresh generation; the _current view returns the newest.
-- ---------------------------------------------------------------------------
