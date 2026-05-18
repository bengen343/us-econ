-- Production SA initial-claims forecast.
--
-- Champion config from backtesting: ARIMA_PLUS on the SA series with a
-- 2023-01-01 training floor, blended 0.6*ARIMA + 0.4*seasonal-naive
-- ("ens_w60"). Horizon 1..13 weeks. Backtest (fair, post-COVID): MAE ~8.9k /
-- MASE ~0.77 on a ~220k series. The SA series is fc_sa_input (DOLETA, with
-- the DOL press advance for the recent weeks DOLETA's XML lags on) so the
-- origin tracks the latest release rather than DOLETA's stale last week.
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
  generated_at TIMESTAMP NOT NULL,            -- when this forecast was produced
  data_through DATE      NOT NULL,            -- last actual SA week it used
  horizon      INT64     NOT NULL,            -- weeks ahead (1..13)
  target_week  DATE       NOT NULL,           -- week being forecast
  sa_forecast  FLOAT64,                       -- ens_w60 = .6*arima + .4*snaive
  arima_sa     FLOAT64,                       -- component (transparency)
  snaive_sa    FLOAT64                        -- component (transparency)
) PARTITION BY data_through;

CREATE OR REPLACE VIEW `us-econ-51920.claims.forecast_sa_initial_claims_current` AS
SELECT *
FROM `us-econ-51920.claims.forecast_sa_initial_claims`
WHERE generated_at = (
  SELECT MAX(generated_at) FROM `us-econ-51920.claims.forecast_sa_initial_claims`
);

-- ---------------------------------------------------------------------------
-- The forecast procedure. Trains on fc_sa_input each run (latest-vintage SA
-- per week, DOLETA preferred with press-advance fallback), so the origin and
-- the coming week always reflect the latest release. Errors are intentionally
-- NOT swallowed -- a scheduled run that fails should surface, not silently skip.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE `us-econ-51920.claims.fc_forecast_sa_initial_claims`()
BEGIN
  DECLARE gen_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP();
  DECLARE today  DATE      DEFAULT CURRENT_DATE();
  DECLARE origin DATE;
  DECLARE opts   STRING    DEFAULT "model_type='ARIMA_PLUS', time_series_timestamp_col='week_ending', time_series_data_col='value', data_frequency='WEEKLY', holiday_region='US'";

  -- Freshest SA week we have (DOLETA, or press advance for the recent weeks
  -- DOLETA's XML lags on) = the forecast origin.
  EXECUTE IMMEDIATE FORMAT("""
    SELECT MAX(week_ending)
    FROM `us-econ-51920.claims.fc_sa_input`
    WHERE week_ending <= DATE '%t'
  """, today) INTO origin;

  -- ARIMA_PLUS on the freshest SA series, 2023-01-01 floor through the origin.
  EXECUTE IMMEDIATE FORMAT("""
    CREATE OR REPLACE MODEL `us-econ-51920.claims.fc_prod_sa_arima`
    OPTIONS(%s) AS
    SELECT week_ending, value
    FROM `us-econ-51920.claims.fc_sa_input`
    WHERE week_ending BETWEEN DATE '2023-01-01' AND DATE '%t'
  """, opts, origin);

  -- Blend 0.6*ARIMA + 0.4*seasonal-naive over horizons 1..13 and append.
  EXECUTE IMMEDIATE FORMAT("""
    INSERT INTO `us-econ-51920.claims.forecast_sa_initial_claims`
    WITH arima AS (
      SELECT DATE(forecast_timestamp) AS target_week,
             DATE_DIFF(DATE(forecast_timestamp), DATE '%t', WEEK) AS horizon,
             forecast_value AS arima_sa
      FROM ML.FORECAST(MODEL `us-econ-51920.claims.fc_prod_sa_arima`,
                       STRUCT(19 AS horizon, 0.9 AS confidence_level))
      WHERE DATE(forecast_timestamp) > DATE '%t'
        AND DATE_DIFF(DATE(forecast_timestamp), DATE '%t', WEEK) BETWEEN 1 AND 13
    ),
    sn AS (
      SELECT week_ending, value
      FROM `us-econ-51920.claims.fc_sa_input`
      WHERE week_ending <= DATE '%t'
    )
    SELECT TIMESTAMP '%t', DATE '%t', a.horizon, a.target_week,
           0.6 * a.arima_sa + 0.4 * sn.value,
           a.arima_sa, sn.value
    FROM arima a
    JOIN sn ON sn.week_ending = DATE_SUB(a.target_week, INTERVAL 364 DAY)
  """, origin, origin, origin, origin, gen_ts, origin);
END;

-- ---------------------------------------------------------------------------
-- Run it now:
--   CALL `us-econ-51920.claims.fc_forecast_sa_initial_claims`();
--   SELECT * FROM `us-econ-51920.claims.forecast_sa_initial_claims_current`
--   ORDER BY horizon;
--
-- Keep it current: create a BigQuery Scheduled Query whose body is just the
-- CALL above, weekly, timed after the claims collector lands the new release
-- (DOLETA updates ~Thursday morning ET). Each run appends a fresh generation;
-- the _current view always returns the newest one.
-- ---------------------------------------------------------------------------
