locals {
  collector_env = {
    GCP_PROJECT = var.project_id
    RAW_BUCKET  = google_storage_bucket.raw.name
    BQ_LOCATION = var.bq_location
  }
}

module "bls_employment" {
  source = "./modules/cloud_run_job"

  name       = "bls-employment"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_employment"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Employment Situation drops first Friday of each month at 08:30 ET (06:30 MT).
  # Cron fires every Friday at 07:30 MT; the collector itself skips Fridays past
  # day 7 of the month. (Unix-cron can't express "first Friday" — restricting both
  # day-of-month and day-of-week is OR'd, not AND'd, so we filter in code.)
  schedule          = "30 7 * * 5"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_editor,
    google_secret_manager_secret_iam_member.runner_bls_key_accessor,
  ]
}

module "claims" {
  source = "./modules/cloud_run_job"

  name       = "claims"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.claims"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # DOL unemployment insurance weekly claims press release drops every Thursday
  # at 08:30 ET (06:30 MT). Cron fires Thursdays at 06:45 MT to give the PDF
  # time to land. The doleta.gov XML archive updates the same morning; the
  # press PDF is generally 1-4 weeks ahead of it, so both feeds are pulled per run.
  schedule          = "45 6 * * 4"
  schedule_timezone = "America/Denver"
  timeout           = "600s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.claims,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_claims_editor,
  ]
}

module "adp_employment_weekly" {
  source = "./modules/cloud_run_job"

  name       = "adp-employment-weekly"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.adp_employment.weekly"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # ADP NER Pulse preliminary estimate releases every Tuesday morning. Cron fires
  # Tuesdays at 06:30 MT.
  schedule          = "30 6 * * 2"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "adp_employment_monthly" {
  source = "./modules/cloud_run_job"

  name       = "adp-employment-monthly"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.adp_employment.monthly"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # ADP NER monthly report drops the first Wednesday of each month. Cron fires
  # every Wednesday at 06:30 MT; the collector itself bails on Wednesdays past
  # day 7 of the month. (Same first-of-month gating pattern as bls_employment.)
  schedule          = "30 6 * * 3"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.adp_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_adp_employment_editor,
  ]
}

module "aaa_gasoline" {
  source = "./modules/cloud_run_job"

  name       = "aaa-gasoline"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.aaa_gasoline"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # AAA refreshes the National Average Gas Prices table daily. Cron fires every
  # day at 06:00 MT.
  schedule          = "0 6 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.aaa_gasoline,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_aaa_gasoline_editor,
  ]
}

module "challenger_employment" {
  source = "./modules/cloud_run_job"

  name       = "challenger-employment"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.challenger_employment"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Challenger Job Cut Announcement Report drops the first Thursday of each
  # month at 08:30 MT (per the user; the PDF itself is embargoed to 07:30 ET).
  # Cron fires every Thursday at 08:30 MT; the collector bails on Thursdays
  # past day 7 of the month.
  schedule          = "30 8 * * 4"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.challenger_employment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_challenger_employment_editor,
  ]
}

module "conference_board" {
  source = "./modules/cloud_run_job"

  name       = "conference-board"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.conference_board"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The Conference Board Consumer Confidence release drops the last Tuesday of
  # each month at ~10:00 ET (08:00 MT). Cron fires every Tuesday at 08:30 MT; the
  # collector itself keeps only the last Tuesday. (Unix-cron can't express "last
  # Tuesday", same in-code gating pattern as bls_employment/challenger.)
  schedule          = "30 8 * * 2"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.conference_board,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_conference_board_editor,
  ]
}

module "michigan_sentiment" {
  source = "./modules/cloud_run_job"

  name       = "michigan-sentiment"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.michigan_sentiment"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The Surveys of Consumers release Fridays at 10:00 ET (preliminary ~2nd
  # Friday of the survey month, final ~4th; exact Fridays shift around
  # holidays). Cron fires every Friday at 08:30 MT (10:30 ET); the collector
  # captures whatever release the homepage shows and merge-upserts on
  # (measure, release_type, observation_month), so non-release Fridays are
  # idempotent rewrites. The official final-history CSVs are re-ingested
  # every run, so a missed FINAL self-heals; a missed PRELIMINARY does not
  # (the homepage is its only public source) -- recover via Wayback snapshot.
  schedule          = "30 8 * * 5"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.michigan_sentiment,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_michigan_sentiment_editor,
  ]
}

module "ism" {
  source = "./modules/cloud_run_job"

  name       = "ism"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.ism"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # ISM releases the Manufacturing Report On Business on the 1st business day of
  # the month and Services on the 3rd, ~10:00 ET (08:00 MT). Cron fires every day
  # in the first week at 08:30 MT; the collector gates each report to its release
  # business day in code (Unix-cron can't express "Nth business day"). Both reports
  # are sourced from ISM's PR Newswire newsroom (ismworld.org is login-gated).
  schedule          = "30 8 1-7 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.ism,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_ism_editor,
  ]
}

module "bls_cpi" {
  source = "./modules/cloud_run_job"

  name       = "bls-cpi"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_cpi"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # CPI for the prior month is released mid-month at 08:30 ET (06:30 MT), on a
  # date that drifts (~10th-15th, any weekday). Cron fires daily at 07:00 MT
  # across the 10-18 window; the collector re-pulls full history each run and is
  # append-only, so off-release runs just add an identical vintage and downstream
  # takes the latest vintage per period. (Unix-cron can't AND day-of-month with
  # weekday, so we widen the day range rather than gate in code.)
  schedule          = "0 7 10-18 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
    google_secret_manager_secret_iam_member.runner_bls_key_accessor,
  ]
}

module "bls_cpi_weights" {
  source = "./modules/cloud_run_job"

  name       = "bls-cpi-weights"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_cpi_weights"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Loads the repo-bundled CPI relative-importance workbook into
  # bls_cpi.relative_importance (BLS publishes RI only as annual xlsx on
  # bot-blocked bls.gov; not in the API). Upserts on (weight_year, population,
  # item_code), so runs are idempotent. RI tables publish annually (~February);
  # cron fires Feb 20 so that when a new weight year is bundled + redeployed it
  # loads in place. Reuses the bls_cpi dataset + its runner IAM (no new dataset).
  schedule          = "0 8 20 2 *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_cpi,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_cpi_editor,
  ]
}

module "bls_ppi" {
  source = "./modules/cloud_run_job"

  name       = "bls-ppi"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_ppi"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # PPI Final Demand for the prior month is released mid-month at 08:30 ET (06:30
  # MT), usually within a day of CPI (~11th-18th). Same widened-window /
  # append-only / latest-vintage approach as bls_cpi.
  schedule          = "0 7 10-18 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_ppi,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_ppi_editor,
    google_secret_manager_secret_iam_member.runner_bls_key_accessor,
  ]
}

module "eia_petroleum" {
  source = "./modules/cloud_run_job"

  name       = "eia-petroleum"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.eia_petroleum"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # EIA refreshes weekly retail gasoline on Mondays (~17:00 ET) and daily crude
  # spot prices on a rolling basis. Cron fires daily at 07:00 MT; the collector
  # re-pulls full history and UPSERTs on (series_id, observation_date), so a run
  # on any day cheaply keeps the table current with no vintage bloat.
  schedule          = "0 7 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.eia_petroleum,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_eia_petroleum_editor,
    google_secret_manager_secret_iam_member.runner_eia_key_accessor,
  ]
}

module "zillow_rent" {
  source = "./modules/cloud_run_job"

  name       = "zillow-rent"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.zillow_rent"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Zillow publishes ZORI monthly, ~2 weeks after month-end (e.g. the March
  # series mid-April). Cron fires daily across the 15-23 window at 08:00 MT; the
  # collector re-pulls the full public CSV and appends a vintage-stamped national
  # series each run, so the release is caught within days regardless of its exact
  # date and downstream takes the latest vintage per month. No API key needed
  # (public CDN CSV).
  schedule          = "0 8 15-23 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.zillow_rent,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_zillow_rent_editor,
  ]
}

module "bls_ntr" {
  source = "./modules/cloud_run_job"

  name       = "bls-ntr"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bls_ntr"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Loads the repo-bundled R-CPI-NTR/ATR seed workbook (no live source: BLS paused
  # publication in 2026-04 and bls.gov is bot-blocked). Upserts on (index_type,
  # observation_date), so runs are idempotent. The R-CPI-NTR/ATR cadence is
  # quarterly (~mid Jan/Apr/Jul/Oct); cron fires the 15th of those months so that
  # if BLS resumes and the bundled workbook is refreshed + redeployed, the new
  # quarters load in place. Until then each run reloads the same static history.
  schedule          = "0 8 15 1,4,7,10 *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bls_ntr,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bls_ntr_editor,
  ]
}

module "energy_futures" {
  source = "./modules/cloud_run_job"

  name       = "energy-futures"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.energy_futures"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # NYMEX RBOB/WTI and ICE Brent futures settle on trading days (~14:30 ET).
  # Cron fires daily at 07:00 MT (09:00 ET), matching eia_petroleum: the
  # collector re-pulls full daily history and UPSERTs on (ticker,
  # observation_date), so each morning run records the prior session's settled
  # close and overwrites the prior day's provisional in-progress bar. No API key
  # (Yahoo public chart endpoint).
  schedule          = "0 7 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.energy_futures,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_energy_futures_editor,
  ]
}

module "census_construction" {
  source = "./modules/cloud_run_job"

  name       = "census-construction"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.census_construction"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The New Residential Construction release lands ~the 16th-19th of each
  # month at 08:30 ET (the exact workday drifts). Cron fires daily at 09:00 MT
  # through that window; each run re-appends the current workbook contents
  # (append-only, vintage-stamped -- consumers dedupe by ingested_at), so
  # pre-release days just restate the prior vintage and the post-release run
  # captures the new month + its two months of revisions.
  schedule          = "0 9 16-20 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.census_construction,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_census_construction_editor,
  ]
}

module "nahb_hmi" {
  source = "./modules/cloud_run_job"

  name       = "nahb-hmi"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.nahb_hmi"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The HMI for month M is released ~the 16th of M (occasionally the 15th or
  # 17th/18th) at 10:00 ET. Cron fires daily at 09:00 MT (11:00 ET) through
  # that window; each run re-appends the full history workbooks (append-only,
  # vintage-stamped -- consumers dedupe by ingested_at), so pre-release days
  # restate the prior vintage and the post-release run captures the new month.
  schedule          = "0 9 15-18 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.nahb_hmi,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_nahb_hmi_editor,
  ]
}

module "bea_vehicles" {
  source = "./modules/cloud_run_job"

  name       = "bea-vehicles"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.bea_vehicles"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # BEA's U70205S table gets month M with the "Supplemental Estimates, Motor
  # Vehicles" update ~the 25th of M+1 (verified 2026-06: the start-of-month
  # auto-sales-day cadence is long gone -- early-month SAARs are private
  # estimators, not BEA). Cron fires daily at 08:00 MT on days 24-28 to
  # capture that update. Full-history re-pull, MERGE upsert on (series_code,
  # observation_month). Requires the free BEA API key (Secret Manager:
  # bea-api-key).
  schedule          = "0 8 24-28 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.bea_vehicles,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_bea_vehicles_editor,
    google_secret_manager_secret_iam_member.runner_bea_key_accessor,
  ]
}

module "census_retail" {
  source = "./modules/cloud_run_job"

  name       = "census-retail"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.census_retail"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # The MARTS advance release lands ~the 15th-17th of each month at 08:30 ET
  # (~10 business days after month end; the exact workday drifts). Cron fires
  # daily at 09:00 MT through that window; each run re-appends the current
  # txt-file contents (append-only, vintage-stamped -- consumers dedupe by
  # ingested_at), so pre-release days restate the prior vintage and the
  # post-release run captures the new month + its MRTS revisions.
  schedule          = "0 9 14-19 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.census_retail,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_census_retail_editor,
  ]
}

module "noaa_climate" {
  source = "./modules/cloud_run_job"

  name       = "noaa-climate"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.noaa_climate"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # NCEI posts month M ~the 8th of M+1. Cron fires at 09:00 MT on days 9-12
  # so the month-M temperature is in BigQuery before the housing-starts
  # forecast's pre-release window (the NRC release lands ~the 16th-19th).
  # Append-only, vintage-stamped; tiny payload (~800 rows).
  schedule          = "0 9 9-12 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.noaa_climate,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_noaa_climate_editor,
  ]
}

module "market_indexes" {
  source = "./modules/cloud_run_job"

  name       = "market-indexes"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.market_indexes"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # S&P 500 daily close for the Michigan-sentiment forecasts. Cron fires daily
  # at 07:00 MT (09:00 ET) matching energy_futures: the collector re-pulls the
  # full daily history (1990+) and UPSERTs on (ticker, observation_date), so
  # each morning run records the prior session's settled close and overwrites
  # any provisional in-progress bar. No API key (Yahoo public chart endpoint).
  schedule          = "0 7 * * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.market_indexes,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_market_indexes_editor,
  ]
}

module "sp_global_pmi" {
  source = "./modules/cloud_run_job"

  name       = "sp-global-pmi"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.sp_global_pmi"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # S&P Global publishes the US PMI as public PDFs on pmi.spglobal.com: the Flash
  # US PMI mid-month (~21st-24th, current survey month) and the final US Services
  # PMI early the next month (~3rd, prior survey month), both embargoed to
  # 13:45/14:45 UTC. Cron fires at 08:30 MT (after the embargo year-round) across
  # both day windows; the collector ingests only a release whose listed date is
  # today, so off-days are cheap no-ops. A missed run self-heals: the next release
  # restates the prior month's value. (Unix-cron can't express "Nth business day"
  # / "fourth Thursday", so we widen the day-of-month ranges and gate in code.)
  schedule          = "30 8 1-7,19-26 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.ism,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_ism_editor,
  ]
}


module "manheim_used_vehicles" {
  source = "./modules/cloud_run_job"

  name       = "manheim-used-vehicles"
  project_id = var.project_id
  region     = var.region

  image = local.image_uri
  args  = ["collectors.manheim_used_vehicles"]
  env   = local.collector_env

  service_account_email = google_service_account.runner.email

  # Cox Automotive posts the full-month Manheim index on the 5th business day
  # of the following month (calendar day 5-9 incl. holiday slips). Cron fires
  # daily at 09:00 MT across a 5-12 window; the collector probes the templated
  # release-post URL (last month first, then the month before) and is a cheap
  # no-op until the post appears. Each successful run re-pulls the spreadsheet's
  # full 1997+ history vintage-stamped, so a missed day self-heals and the CPI
  # forecast (05:00 MT, days 1-15) picks the value up the morning after it
  # lands -- ahead of the mid-month CPI release. (Unix-cron can't express "Nth
  # business day", so we widen the window and gate in code.)
  schedule          = "0 9 5-12 * *"
  schedule_timezone = "America/Denver"
  timeout           = "300s"
  memory            = "512Mi"
  cpu               = "1"

  depends_on = [
    google_bigquery_dataset.manheim_used_vehicles,
    google_storage_bucket_iam_member.runner_raw_writer,
    google_bigquery_dataset_iam_member.runner_manheim_used_vehicles_editor,
  ]
}
