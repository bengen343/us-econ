-- Phase-2: fair h=1 head-to-head on IDENTICAL origins (same 96 weekly origins
-- as 20_timesfm_test), latest-data basis (fct_sa_input), scored vs first-print
-- (fct_actuals_as_reported). Adds the real ARIMA/ensemble champion (per-origin
-- ARIMA loop -- the expensive part: ~96 model trains, budget ~10-15 min) and
-- TimesFM(2.5)+ARIMA blends so we can see whether combination wins again.
--
-- Throwaway artifacts to DROP when done: claims.fct_phase2,
-- claims.fct_p2_arima, model claims.fct_tmp_p2_arima (+ fct_timesfm_test).

DECLARE eval_start DATE DEFAULT DATE '2024-07-01';
DECLARE eval_end   DATE;
DECLARE opts STRING DEFAULT "model_type='ARIMA_PLUS', time_series_timestamp_col='week_ending', time_series_data_col='value', data_frequency='WEEKLY', holiday_region='US'";

SET eval_end = (
  SELECT DATE_SUB(MAX(week_ending), INTERVAL 7 DAY)
  FROM `us-econ-51920.claims.fct_actuals_as_reported`
);

CREATE TEMP TABLE origins AS
SELECT week_ending AS origin
FROM `us-econ-51920.claims.fct_sa_input`
WHERE week_ending BETWEEN eval_start AND eval_end;

-- TimesFM 2.5 (best Phase-1 variant), regenerated on these origins.
CREATE TEMP TABLE panel_floor AS
SELECT FORMAT_DATE('%Y-%m-%d', o.origin) AS origin,
       TIMESTAMP(s.week_ending) AS ts, s.value AS sa
FROM origins o
JOIN `us-econ-51920.claims.fct_sa_input` s
  ON s.week_ending BETWEEN DATE '2023-01-01' AND o.origin;

CREATE TEMP TABLE tf AS
SELECT PARSE_DATE('%Y-%m-%d', origin) AS origin,
       DATE(forecast_timestamp) AS target_week,
       forecast_value AS tf25
FROM AI.FORECAST(TABLE panel_floor,
       data_col => 'sa', timestamp_col => 'ts', id_cols => ['origin'],
       model => 'TimesFM 2.5', horizon => 1);

-- ARIMA_PLUS champion leg: train per origin (2023-01-01 floor), take h=1.
CREATE OR REPLACE TABLE `us-econ-51920.claims.fct_p2_arima` (
  origin DATE, target_week DATE, arima FLOAT64
);

FOR rec IN (SELECT origin FROM origins ORDER BY origin) DO
  BEGIN
    EXECUTE IMMEDIATE FORMAT("""
      CREATE OR REPLACE MODEL `us-econ-51920.claims.fct_tmp_p2_arima`
      OPTIONS(%s) AS
      SELECT week_ending, value
      FROM `us-econ-51920.claims.fct_sa_input`
      WHERE week_ending BETWEEN DATE '2023-01-01' AND DATE '%t'
    """, opts, rec.origin);

    EXECUTE IMMEDIATE FORMAT("""
      INSERT INTO `us-econ-51920.claims.fct_p2_arima`
      SELECT DATE '%t', DATE(forecast_timestamp), forecast_value
      FROM ML.FORECAST(MODEL `us-econ-51920.claims.fct_tmp_p2_arima`,
                       STRUCT(3 AS horizon, 0.9 AS confidence_level))
      WHERE DATE_DIFF(DATE(forecast_timestamp), DATE '%t', WEEK) = 1
    """, rec.origin, rec.origin);
  EXCEPTION WHEN ERROR THEN
    SELECT CONCAT('arima skipped origin ', CAST(rec.origin AS STRING),
                  ': ', @@error.message);
  END;
END FOR;

-- Wide per-origin table, then score every method/blend in one pass.
CREATE OR REPLACE TABLE `us-econ-51920.claims.fct_phase2` AS
WITH w AS (
  SELECT
    o.origin,
    DATE_ADD(o.origin, INTERVAL 7 DAY) AS target_week,
    a.arima,
    t.tf25,
    sn.value AS snaive,
    rwv.value AS rw
  FROM origins o
  LEFT JOIN `us-econ-51920.claims.fct_p2_arima` a ON a.origin = o.origin
  LEFT JOIN tf t ON t.origin = o.origin
  LEFT JOIN `us-econ-51920.claims.fct_sa_input` sn
    ON sn.week_ending = DATE_SUB(DATE_ADD(o.origin, INTERVAL 7 DAY), INTERVAL 364 DAY)
  LEFT JOIN `us-econ-51920.claims.fct_sa_input` rwv
    ON rwv.week_ending = o.origin
),
scored AS (
  SELECT w.*, act.sa_as_reported AS actual
  FROM w
  JOIN `us-econ-51920.claims.fct_actuals_as_reported` act
    ON act.week_ending = w.target_week
)
SELECT
  m.method,
  COUNT(*)                                                AS n,
  CAST(ROUND(AVG(ABS(m.pred - actual))) AS INT64)         AS mae,
  CAST(ROUND(SQRT(AVG(POW(m.pred - actual, 2)))) AS INT64) AS rmse,
  CAST(ROUND(AVG(m.pred - actual)) AS INT64)              AS bias
FROM scored,
UNNEST([
  STRUCT('arima'             AS method, scored.arima                                   AS pred),
  STRUCT('ens_w60_champion',          0.6*scored.arima + 0.4*scored.snaive),
  STRUCT('tf25',                      scored.tf25),
  STRUCT('snaive',                    scored.snaive),
  STRUCT('rw',                        scored.rw),
  STRUCT('blend_tf30_ar70',           0.3*scored.tf25 + 0.7*scored.arima),
  STRUCT('blend_tf50_ar50',           0.5*scored.tf25 + 0.5*scored.arima),
  STRUCT('blend_tf70_ar30',           0.7*scored.tf25 + 0.3*scored.arima),
  STRUCT('blend_tf50_ensw60',         0.5*scored.tf25 + 0.5*(0.6*scored.arima + 0.4*scored.snaive))
]) AS m
WHERE m.pred IS NOT NULL
GROUP BY m.method
ORDER BY mae;
