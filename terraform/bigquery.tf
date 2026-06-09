# Datasets only. Tables are created on first load by the collectors via
# CREATE_IF_NEEDED, so the Python schema stays the single source of truth.

resource "google_bigquery_dataset" "bls" {
  dataset_id  = "bls_employment"
  description = "BLS monthly payrolls."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "claims" {
  dataset_id  = "claims"
  description = "DOL weekly unemployment insurance claims (national + state, full revision history)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "adp_employment" {
  dataset_id  = "adp_employment"
  description = "ADP National Employment Report: monthly NER history (CSV) + weekly preliminary estimates (NER Pulse)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "challenger_employment" {
  dataset_id  = "challenger_employment"
  description = "Challenger, Gray & Christmas monthly job cut announcement report (parsed from PDF)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "aaa_gasoline" {
  dataset_id  = "aaa_gasoline"
  description = "AAA national average retail gasoline prices, scraped daily from gasprices.aaa.com."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "rcp_potus_approval" {
  dataset_id  = "rcp_potus_approval"
  description = "RealClearPolling presidential approval poll table snapshots (append-only daily captures)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "google_trends" {
  dataset_id  = "google_trends"
  description = "Google Trends weekly search-interest indices (full 5-yr re-pull per run, vintage-stamped) -- forecast inputs for claims, gasoline, and other series."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "conference_board" {
  dataset_id  = "conference_board"
  description = "The Conference Board Consumer Confidence release: monthly index + survey-share series parsed from the press release (append-only, latest-month-per-run)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "ism" {
  dataset_id  = "ism"
  description = "Monthly business-survey diffusion indexes: ISM Report On Business (Manufacturing + Services, table report_on_business) and S&P Global US PMI flash + final (table sp_global_us_pmi), parsed from the respective press releases (append-only)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "bls_cpi" {
  dataset_id  = "bls_cpi"
  description = "BLS Consumer Price Index (CPI-U): headline, core, and component index levels (SA + NSA) with API-supplied 1/3/12-month percent changes, from the BLS API v2 (append-only, vintage-stamped)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "bls_ppi" {
  dataset_id  = "bls_ppi"
  description = "BLS Producer Price Index Final Demand-Intermediate Demand (FD-ID): headline, core, and component index levels (SA + NSA) with API-supplied 1/3/12-month percent changes, from the BLS API v2 (append-only, vintage-stamped)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "eia_petroleum" {
  dataset_id  = "eia_petroleum"
  description = "EIA petroleum, from the EIA API v2, upserted on (series_id, observation_date) -- EIA series are not meaningfully revised. Table `prices`: weekly U.S. retail gasoline (all grades + regular/midgrade/premium) and No. 2 diesel, daily WTI and Brent crude spot, and daily U.S. gasoline spot (NY Harbor + Gulf Coast conventional regular, LA RBOB regular). Table `supply`: weekly total motor gasoline ending stocks (thousand barrels) and refinery percent utilization of operable capacity."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "zillow_rent" {
  dataset_id  = "zillow_rent"
  description = "Zillow Observed Rent Index (ZORI), national, smoothed (SA + NSA), from Zillow Research public CSVs -- a market-rent leading indicator for nowcasting CPI shelter (append-only, vintage-stamped to preserve ZORI revisions)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "bls_ntr" {
  dataset_id  = "bls_ntr"
  description = "BLS research New Tenant Rent (R-CPI-NTR) and All Tenant Regressed Rent (R-CPI-ATR) quarterly indices -- the cleanest structural lead of CPI rent (built from CPI Housing Survey microdata). Loaded from a repo-bundled xlsx seed (BLS publication paused 2026-04; bls.gov is bot-blocked, no live fetch), upserted on (index_type, observation_date)."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}

resource "google_bigquery_dataset" "energy_futures" {
  dataset_id  = "energy_futures"
  description = "Front-month energy futures daily OHLCV from the Yahoo Finance chart API: RBOB gasoline (RB=F), WTI crude (CL=F), and Brent crude (BZ=F). The low-latency daily market signal for the AAA gasoline next-day forecast (RBOB is the benchmark retail tracks with a lag). Upserted on (ticker, observation_date) -- a provisional in-progress close is overwritten by the settled value on the next run."
  location    = var.bq_location

  depends_on = [google_project_service.enabled]
}
