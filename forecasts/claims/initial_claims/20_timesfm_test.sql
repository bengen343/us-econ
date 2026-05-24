-- Phase-1 screening: is TimesFM (AI.FORECAST) competitive at HORIZON 1?
--
-- Walk-forward over recent weekly origins. At each origin we feed the SA
-- history into AI.FORECAST and take the 1-week-ahead point, then score it
-- against the first-print actual. Naive baselines (snaive, rw) computed on the
-- IDENTICAL origins for a fair floor. No ARIMA/ens here on purpose: if TimesFM
-- can't beat naive and get near the 5,000 MAE goal, this is a cheap stop; if
-- it can, Phase 2 adds the ARIMA/ens champion on the same origins.
--
-- Honest caveats:
--  * Latest-data backtest (fct_sa_input = freshest SA), so mildly optimistic --
--    same basis as our use_pit=FALSE runs, consistent for comparison.
--  * TimesFM is pretrained; US initial claims is a famous public series that
--    may be in its training corpus, so zero-shot here can be optimistically
--    biased vs a truly novel series. Treat a good result as an upper bound.
--  * id_cols => ['origin'] uses a DATE id column; if AI.FORECAST rejects a
--    non-STRING id, cast origin to STRING in the panels and parse back.
--
-- Results land in claims.fct_timesfm_test (a throwaway artifact -- DROP when
-- done). AI.FORECAST is billed model inference; this is ~3 variants x ~95
-- single-step series, cheap but non-zero.

DECLARE eval_start DATE DEFAULT DATE '2024-07-01';
DECLARE eval_end   DATE;

-- Latest week we have a first-print actual for, less one week so every
-- origin's h=1 target has a value to score against.
SET eval_end = (
  SELECT DATE_SUB(MAX(week_ending), INTERVAL 7 DAY)
  FROM `us-econ-51920.claims.fct_actuals_as_reported`
);

CREATE TEMP TABLE origins AS
SELECT week_ending AS origin
FROM `us-econ-51920.claims.fct_sa_input`
WHERE week_ending BETWEEN eval_start AND eval_end;

-- Per-origin SA history, tagged by origin so id_cols forecasts them all in
-- one call. Two context variants: the champion's 2023-01-01 floor, and full
-- history (TimesFM is pretrained/robust and may exploit longer context).
CREATE TEMP TABLE panel_floor AS
SELECT FORMAT_DATE('%Y-%m-%d', o.origin) AS origin,
       TIMESTAMP(s.week_ending) AS ts, s.value AS sa
FROM origins o
JOIN `us-econ-51920.claims.fct_sa_input` s
  ON s.week_ending BETWEEN DATE '2023-01-01' AND o.origin;

CREATE TEMP TABLE panel_full AS
SELECT FORMAT_DATE('%Y-%m-%d', o.origin) AS origin,
       TIMESTAMP(s.week_ending) AS ts, s.value AS sa
FROM origins o
JOIN `us-econ-51920.claims.fct_sa_input` s
  ON s.week_ending <= o.origin;

CREATE TEMP TABLE fc AS
SELECT 'timesfm20_floor' AS method, PARSE_DATE('%Y-%m-%d', origin) AS origin,
       DATE(forecast_timestamp) AS target_week, forecast_value AS pred
FROM AI.FORECAST(TABLE panel_floor,
       data_col => 'sa', timestamp_col => 'ts', id_cols => ['origin'],
       model => 'TimesFM 2.0', horizon => 1)
UNION ALL
SELECT 'timesfm25_floor', PARSE_DATE('%Y-%m-%d', origin),
       DATE(forecast_timestamp), forecast_value
FROM AI.FORECAST(TABLE panel_floor,
       data_col => 'sa', timestamp_col => 'ts', id_cols => ['origin'],
       model => 'TimesFM 2.5', horizon => 1)
UNION ALL
SELECT 'timesfm20_full', PARSE_DATE('%Y-%m-%d', origin),
       DATE(forecast_timestamp), forecast_value
FROM AI.FORECAST(TABLE panel_full,
       data_col => 'sa', timestamp_col => 'ts', id_cols => ['origin'],
       model => 'TimesFM 2.0', horizon => 1);

CREATE TEMP TABLE base AS
SELECT 'snaive' AS method, o.origin,
       DATE_ADD(o.origin, INTERVAL 7 DAY) AS target_week, s.value AS pred
FROM origins o
JOIN `us-econ-51920.claims.fct_sa_input` s
  ON s.week_ending = DATE_SUB(DATE_ADD(o.origin, INTERVAL 7 DAY), INTERVAL 364 DAY)
UNION ALL
SELECT 'rw', o.origin, DATE_ADD(o.origin, INTERVAL 7 DAY), s.value
FROM origins o
JOIN `us-econ-51920.claims.fct_sa_input` s ON s.week_ending = o.origin;

CREATE OR REPLACE TABLE `us-econ-51920.claims.fct_timesfm_test` AS
WITH all_fc AS (
  SELECT method, origin, target_week, pred FROM fc
  UNION ALL
  SELECT method, origin, target_week, pred FROM base
)
SELECT
  f.method,
  COUNT(*)                                                      AS n,
  CAST(ROUND(AVG(ABS(f.pred - a.sa_as_reported))) AS INT64)     AS mae,
  CAST(ROUND(SQRT(AVG(POW(f.pred - a.sa_as_reported, 2)))) AS INT64) AS rmse,
  CAST(ROUND(AVG(f.pred - a.sa_as_reported)) AS INT64)          AS bias,
  CAST(ROUND(MAX(ABS(f.pred - a.sa_as_reported))) AS INT64)     AS worst_abs_err
FROM all_fc f
JOIN `us-econ-51920.claims.fct_actuals_as_reported` a
  ON a.week_ending = f.target_week
GROUP BY f.method
ORDER BY mae;
