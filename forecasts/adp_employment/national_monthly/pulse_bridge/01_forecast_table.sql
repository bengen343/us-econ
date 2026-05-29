-- Output table + current-view for the ADP headline Pulse-bridge forecast.
--
-- The Cloud Run Job (forecasts.adp_employment.national_monthly.pulse_bridge)
-- self-bootstraps these with the identical DDL on first run, so applying this
-- file by hand is optional — it exists for documentation and manual setup.
--
-- Append-only with weekly revisions: one row per
-- (target_month, as_of_pulse_week, model_version). Each Tuesday's run upserts
-- the row for the latest Pulse week, so the table records the full
-- forecast-revision trajectory for every target month.

CREATE TABLE IF NOT EXISTS `us-econ-51920.adp_employment.forecast_national_monthly` (
  target_month      DATE      NOT NULL,  -- month whose headline is forecast (first-of-month)
  generated_at      TIMESTAMP NOT NULL,  -- when this row was computed
  as_of_pulse_week  DATE,                -- latest Pulse week_ending used (the "revision")
  headline_forecast FLOAT64,             -- blended point forecast (the deliverable)
  prior_component   FLOAT64,             -- random-walk prior (last released headline)
  pulse_component   FLOAT64,             -- calibrated Pulse bridge (b * implied)
  blend_weight      FLOAT64,             -- w: fraction of target month observed
  pulse_run_rate    FLOAT64,             -- mean Pulse 4wk-MA over observed weeks in M
  pulse_implied     FLOAT64,             -- run_rate * expected_weeks
  pulse_weeks_used  INT64,
  expected_weeks    INT64,               -- Saturday week-endings in month M
  calib_scale       FLOAT64,             -- shrunk bridge scale b actually used
  calib_raw_scale   FLOAT64,             -- unshrunk pooled ratio (diagnostic)
  calib_n_months    INT64,               -- complete-Pulse months in calibration
  model_version     STRING,
  run_id            STRING
)
PARTITION BY target_month
OPTIONS (description = 'ADP national-monthly headline Pulse-bridge forecast; append-only weekly revisions, one row per (target_month, as_of_pulse_week, model_version).');

CREATE OR REPLACE VIEW `us-econ-51920.adp_employment.forecast_national_monthly_current` AS
SELECT * EXCEPT(rn) FROM (
  SELECT *, ROW_NUMBER() OVER (
              PARTITION BY target_month
              ORDER BY generated_at DESC) AS rn
  FROM `us-econ-51920.adp_employment.forecast_national_monthly`
) WHERE rn = 1;
