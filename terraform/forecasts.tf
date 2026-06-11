# Python-based forecasts run as Cloud Run Jobs in the same container image
# as the collectors. The existing SQL-based forecasts (the ARIMA+TimesFM
# stored procedure called by a BigQuery Scheduled Query) are unaffected by
# this — these Python jobs run *after* them and UPDATE the same generation's
# h=1 row with direction-prediction columns.

module "direction_lgbm_initial_claims" {
  source = "./modules/cloud_run_job"

  name       = "forecast-direction-initial-claims"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.claims.initial_claims.direction_lgbm"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Initial claims press release drops Thursday 08:30 ET. The existing
  # forecast_sa_initial_claims Scheduled Query runs sometime before this. We
  # fire at 17:30 UTC Thursdays — a safe buffer after both the data release
  # and the level-forecast scheduled query, before US-Pacific business hours
  # consumers wake up.
  schedule          = "30 17 * * 4"
  schedule_timezone = "Etc/UTC"

  # LGBM walk-forward calibration loop refits the model ~26 times for the
  # calibration history (one fit per origin in the prior 26 weeks) plus the
  # final fit. ~10s per fit on our data => 4-5 min worst case.
  timeout = "600s"
  memory  = "1Gi"
  cpu     = "1"

  depends_on = [
    google_bigquery_dataset.claims,
    google_bigquery_dataset.google_trends,
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset_iam_member.runner_claims_editor,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
    google_bigquery_dataset_iam_member.runner_google_trends_viewer,
  ]
}

module "adp_headline_pulse_bridge" {
  source = "./modules/cloud_run_job"

  name       = "forecast-adp-headline-pulse"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.adp_employment.national_monthly.pulse_bridge"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The NER Pulse preliminary estimate is collected Tuesdays at 06:30 MT
  # (module "adp_employment_weekly"). Fire at 07:30 MT Tuesdays so the freshest
  # Pulse vintage has landed; the job re-nowcasts the next unreleased monthly
  # headline and upserts a revision row. Runs are cheap + idempotent, so weeks
  # with no new Pulse vintage simply rewrite the same row with a new timestamp.
  # The job also self-bootstraps its output table + _current view on first run.
  schedule          = "30 7 * * 2"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "employment_situation_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-employment-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_employment.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released Employment Situation (NFP MoM change +
  # unemployment rate) from a small model ensemble. The release is the first
  # Friday at 08:30 ET (06:30 MT); the BLS collector loads it at 07:30 MT. We
  # fire every day in the first week at 05:00 MT — before the release — and the
  # job gates in code to weekdays on/before the first Friday, re-running each day
  # so the forecast firms up as Conference Board / ISM / ADP / claims inputs land.
  # The DFM refits each run (~20s). Self-bootstraps its table + _current view.
  schedule          = "0 5 1-7 * *"
  schedule_timezone = "America/Denver"
  timeout           = "600s"
  memory            = "1Gi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls,
    google_bigquery_dataset.claims,
    google_bigquery_dataset.adp_employment,
    google_bigquery_dataset.conference_board,
    google_bigquery_dataset.ism,
    google_bigquery_dataset.google_trends,
    google_bigquery_dataset_iam_member.runner_bls_editor,
  ]
}

module "cpi_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-cpi-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released CPI (headline & core, m/m and y/y) with the
  # deterministic bottom-up reconstruction. CPI for the prior month releases
  # ~the 10th-15th at 08:30 ET (06:30 MT). We fire daily on days 1-15 at 05:00 MT
  # — before the release (and before the bls_cpi collector loads it at 07:00 MT) —
  # and the job gates in code to the first ~18 days, re-running each day so the
  # nowcast firms up as the month's EIA fuel prices finalise. Light + idempotent
  # (no model fitting; upsert by as_of_date). Self-bootstraps its table + _current
  # view. Reads bls_cpi (cpi_series + relative_importance) and eia_petroleum.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
  ]
}

module "eggs_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-eggs-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.eggs.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released BLS average egg price ($/dozen level and
  # m/m %) with an AR + PPI-chicken-eggs distributed lag + seasonal OLS (see
  # forecasts/bls_cpi/eggs). The AP print releases with CPI ~the 10th-15th; the
  # M-1 PPI regressor landed mid-prior-month, so the nowcast is stable across
  # the window. Same cadence as the CPI forecast: daily on days 1-15 at 05:00
  # MT, gated in code to the first ~18 days, idempotent upsert by as_of_date.
  # Self-bootstraps its table + _current view. Reads bls_cpi.average_prices and
  # bls_ppi.ppi_series.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset.bls_ppi,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_bigquery_dataset_iam_member.runner_bls_ppi_editor,
  ]
}

module "gasoline_cpi_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-gasoline-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.gasoline.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released CPI gasoline index (CUSR0000SETB01, SA
  # level + m/m %) from the target month's own complete EIA weekly retail
  # prices plus the calendar-month SA wedge (see forecasts/bls_cpi/gasoline).
  # Distinct from aaa-gasoline-forecast (next-day retail price): this one
  # targets the monthly CPI component. Same cadence as the CPI forecast: daily
  # on days 1-15 at 05:00 MT, gated in code to the first ~18 days, idempotent
  # upsert by as_of_date. Self-bootstraps its table + _current view. Reads
  # bls_cpi.cpi_series and eia_petroleum.prices.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
  ]
}

module "shelter_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-shelter-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.shelter.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released CPI shelter index (CUSR0000SAH1, SA
  # level + m/m %) with the trailing-6-month-mean nowcast (see
  # forecasts/bls_cpi/shelter -- persistence beat every fitted spec and all
  # ZORI market-rent features at the one-month horizon). Same cadence as the
  # CPI forecast: daily on days 1-15 at 05:00 MT, gated in code to the first
  # ~18 days, idempotent upsert by as_of_date. Self-bootstraps its table +
  # _current view. Reads bls_cpi.cpi_series only.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
  ]
}

module "electricity_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-electricity-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.electricity.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released BLS average electricity price
  # (APU000072610, $/kWh level + m/m %) with an own-history seasonal AR OLS
  # (see forecasts/bls_cpi/electricity -- administered retail rates carry no
  # exploitable PPI/natural-gas signal at h=1; persistence + the summer
  # rate-schedule seasonality won the bake-off). Same cadence as the CPI
  # forecast: daily on days 1-15 at 05:00 MT, gated in code to the first ~18
  # days, idempotent upsert by as_of_date. Self-bootstraps its table +
  # _current view. Reads bls_cpi.average_prices only.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
  ]
}

module "airfares_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-airfares-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_cpi.airfares.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released CPI airline fares index (CUSR0000SETG01,
  # SA level + m/m %) with an AR(2) + WTI distributed-lag OLS (see
  # forecasts/bls_cpi/airfares -- fares m/m mean-reverts and month-M WTI is
  # fully published before the release; PPI airline and jet-fuel specs lost
  # the bake-off). Same cadence as the CPI forecast: daily on days 1-15 at
  # 05:00 MT, gated in code to the first ~18 days, idempotent upsert by
  # as_of_date. Self-bootstraps its table + _current view. Reads
  # bls_cpi.cpi_series and eia_petroleum.prices.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
  ]
}

module "ppi_headline_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bls-ppi-headline-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bls_ppi.headline_yy.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the about-to-be-released headline PPI final demand y/y (WPUFD4,
  # NSA; plus m/m and the index level) one month ahead. Only the month-M m/m
  # is unknown -- the 12-month base is published -- and PPI prices reference
  # the Tuesday of the week containing the 13th, so the winning OLS uses
  # gasoline-spot + diesel changes dated to that pricing date plus the month-M
  # ISM mfg prices-paid print (released the 1st business day of M+1, before
  # the PPI). 2017-2026 COVID-masked backtest: y/y RMSE 0.29pp vs 0.58 for the
  # y/y random walk. Same cadence as the CPI forecasts: daily on days 1-15 at
  # 05:00 MT, gated in code to the first ~18 days, idempotent upsert by
  # as_of_date; idles until the month-M ISM print lands. Self-bootstraps its
  # table + _current view. Reads bls_ppi.ppi_series, eia_petroleum.prices, and
  # ism.report_on_business; writes bls_ppi.forecast_headline.
  schedule          = "0 5 1-15 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_ppi,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset.ism,
    google_bigquery_dataset_iam_member.runner_bls_ppi_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
    google_bigquery_dataset_iam_member.runner_ism_editor,
  ]
}

module "michigan_sentiment_forecast" {
  source = "./modules/cloud_run_job"

  name       = "michigan-sentiment-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.michigan_sentiment.headline.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the pending Michigan ICS release -- the next preliminary (vs
  # the prior final) or the current month's final (the revision vs the
  # published prelim); the pending target alternates so each run emits one.
  # Winning OLS specs use gasoline-spot + S&P 500 changes over the survey
  # interview windows (prelim interviews ~25th of M-1 .. 7th of M; the final
  # adds ~days 8-21). 2010-2026 COVID-masked: prelim RMSE 3.61 vs 3.89
  # carry-forward; revision RMSE 1.35 vs 1.43 zero-revision, ~64% direction.
  # Daily at 07:30 MT, after the 07:00 eia_petroleum + market_indexes
  # collections; idempotent upsert by as_of_date. Self-bootstraps its table +
  # _current view. Reads michigan_sentiment.surveys_of_consumers,
  # eia_petroleum.prices, market_indexes.daily; writes
  # michigan_sentiment.forecast_headline.
  schedule          = "30 7 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.michigan_sentiment,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset.market_indexes,
    google_bigquery_dataset_iam_member.runner_michigan_sentiment_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
    google_bigquery_dataset_iam_member.runner_market_indexes_editor,
  ]
}

module "starts_permits_forecast" {
  source = "./modules/cloud_run_job"

  name       = "census-starts-permits-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.census_construction.starts_permits.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts next month's housing starts + building permits (SAAR level +
  # m/m % each) ahead of the joint NRC release (~16th-19th, 08:30 ET).
  # Starts: permits/starts ECM gap + month-M NAHB HMI + NOAA temperature
  # deviations (2010-2026 COVID-masked: m/m RMSE 6.27 vs 8.24 carry-forward,
  # ~76% direction). Permits: SF/MF-split AR (4.63 vs 5.03). Daily at 10:00
  # MT days 1-20 (gated in code to day <= 20), after the 09:00 collector
  # runs; the permits spec completes right after the prior release, the
  # starts spec once the month-M temperature posts (~the 9th). Idempotent
  # upsert by as_of_date; self-bootstraps its table + _current view. Reads
  # census_construction, nahb_hmi, noaa_climate; writes
  # census_construction.forecast_starts_permits.
  schedule          = "0 10 1-20 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.census_construction,
    google_bigquery_dataset.nahb_hmi,
    google_bigquery_dataset.noaa_climate,
    google_bigquery_dataset_iam_member.runner_census_construction_editor,
    google_bigquery_dataset_iam_member.runner_nahb_hmi_editor,
    google_bigquery_dataset_iam_member.runner_noaa_climate_editor,
  ]
}

module "core_pce_forecast" {
  source = "./modules/cloud_run_job"

  name       = "bea-core-pce-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.bea_pce.core_mm.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts core PCE m/m (DPCCRG) for the next Personal Income release
  # (~25th-31st of M+1) via the CPI/PPI translation OLS: month-M core CPI +
  # month-M PPI airfares, both published mid-M+1, weeks before the origin.
  # 2012-2026 COVID-masked: MAE 0.052pp / RMSE 0.072 vs 0.101 AR(1) and
  # 0.233 carry-forward. Daily at 10:30 MT (after the morning collectors);
  # the window self-gates -- between a PCE release and the next month's
  # CPI/PPI prints the shared model returns None and the job idles.
  # Idempotent upsert by as_of_date; self-bootstraps its table + _current
  # view. Reads bea_pce.price_indexes, bls_cpi.cpi_series,
  # bls_ppi.ppi_series; writes bea_pce.forecast_core_pce.
  schedule          = "30 10 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bea_pce,
    google_bigquery_dataset.bls_cpi,
    google_bigquery_dataset.bls_ppi,
    google_bigquery_dataset_iam_member.runner_bea_pce_editor,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_bigquery_dataset_iam_member.runner_bls_ppi_editor,
  ]
}

module "ism_mfg_forecast" {
  source = "./modules/cloud_run_job"

  name       = "ism-mfg-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.ism.manufacturing_pmi.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Forecasts the headline ISM Manufacturing PMI for month M (released the
  # 1st business day of M+1, 10:00 ET) from the month-M S&P flash gap + the
  # equal-weight regional-Fed-survey composite gap + the own lag. Flash-era
  # backtest (2018+, COVID-masked): change RMSE 1.07 vs 1.20 random walk.
  # Daily at 11:00 MT; the window self-gates -- before the month-M flash
  # (~21st-24th) the shared model returns None and the job idles, then
  # re-nowcasts daily as the late Fed surveys (Richmond, Dallas) arrive.
  # Idempotent upsert by as_of_date; self-bootstraps its table + _current
  # view. Reads ism.report_on_business, ism.sp_global_us_pmi,
  # fed_surveys.manufacturing_surveys; writes ism.forecast_manufacturing_pmi.
  schedule          = "0 11 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.ism,
    google_bigquery_dataset.fed_surveys,
    google_bigquery_dataset_iam_member.runner_ism_editor,
    google_bigquery_dataset_iam_member.runner_fed_surveys_editor,
  ]
}

module "aaa_gasoline_forecast" {
  source = "./modules/cloud_run_job"

  name       = "aaa-gasoline-forecast"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["forecasts.aaa_gasoline.next_day.production"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Next-day (h=1) AAA national-average regular forecast from the symmetric RBOB
  # error-correction model. Fits cointegration + short-run dynamics on the long
  # EIA weekly retail history, then applies it to the latest AAA level + RBOB
  # settle. Inputs land in the morning -- AAA scrape 06:00 MT, eia_petroleum +
  # energy_futures 07:00 MT -- so we fire daily at 08:00 MT against the freshest
  # anchor. Light + idempotent (small OLS; upsert by as_of_date). Self-bootstraps
  # its table + _current view. Reads eia_petroleum + energy_futures, writes
  # aaa_gasoline.
  schedule          = "0 8 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.aaa_gasoline,
    google_bigquery_dataset.eia_petroleum,
    google_bigquery_dataset.energy_futures,
    google_bigquery_dataset_iam_member.runner_aaa_gasoline_editor,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
    google_bigquery_dataset_iam_member.runner_energy_futures_editor,
  ]
}
