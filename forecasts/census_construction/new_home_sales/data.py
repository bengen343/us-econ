"""Data pulls for the new-home-sales forecast (research harness; production
reads BigQuery once the collectors populate).

Target: new single-family houses sold (SAAR, thousands) for month M -- the
headline of the Census/HUD New Residential Sales release, ~the 23rd-27th of
M+1 (10:00 ET). The series is the noisiest housing print: the preliminary SA
estimate revises ~5% on average and the sample is literally drawn from
building permits.

Candidate inputs, all published before the origin:

  * SF permits + SF starts for the SAME month M -- the NRC release lands
    ~the 17th of M+1, a week before New Residential Sales. The mechanical
    link (the sales sample is permit-drawn) makes same-month SF permits the
    structural lever.
  * NAHB HMI and its "SF sales: present" component -- builders' read on
    exactly this series. Month M is released ~the 16th of M; the M+1 print
    (~16th of M+1) is ALSO out before the release and is tested as a
    leading variant.
  * 30-yr mortgage rate (Freddie PMMS weekly file): affordability.
  * Months' supply (for-sale inventory / sales), lag 1: mean-reversion
    state. From fsale_cust.xlsx + sold_cust.xlsx.

Harness sources are the same official files the collectors use (census.gov
workbooks; the NAHB t2/t3 workbooks via the collector's own parser; Freddie
xlsx) -- the BigQuery copies were deployed today and fill from ~the 15th.
"""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from forecasts.census_construction.starts_permits.data import (
    _get,
    pull_census,
    pull_pmms,
)

PROJECT = "us-econ-51920"
SOLD_URL = "https://www.census.gov/construction/nrs/xls/sold_cust.xlsx"
FSALE_URL = "https://www.census.gov/construction/nrs/xls/fsale_cust.xlsx"


def pull_nrs(url: str) -> pd.Series:
    """The US seasonally adjusted series of a New Residential Sales workbook
    ('Monthly' sheet: col 0 = month, col 1 = NSA US, col 6 = SA US -- annual
    rate for sales, end-of-month level for the for-sale stock)."""
    raw = pd.read_excel(io.BytesIO(_get(url)), sheet_name="Monthly", header=None)
    months = pd.to_datetime(raw.iloc[6:, 0], errors="coerce")
    values = pd.to_numeric(raw.iloc[6:, 6], errors="coerce")
    keep = months.notna() & values.notna()
    return pd.Series(values[keep].to_numpy(), index=months[keep]).sort_index()


def pull_hmi_components() -> pd.DataFrame:
    """National HMI + the SF-sales-present component via the nahb_hmi
    collector's own fetch+parse (the BigQuery table fills from ~the 15th)."""
    from collectors.common.http import client
    from collectors.nahb_hmi import collect as nahb

    with client(timeout=120.0) as http:
        page = nahb._get(http, nahb.HMI_PAGE).decode("utf-8", errors="replace")
        rows = [
            *nahb._parse_t2(nahb._get(http, nahb._discover(page, nahb.T2_RE, "t2"))),
            *nahb._parse_t3(nahb._get(http, nahb._discover(page, nahb.T3_RE, "t3"))),
        ]
    frame = pd.DataFrame(rows)
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="measure", values="value")
    return wide[["hmi", "sf_sales_present"]]


def pull_panel(cache: str | Path | None = None) -> pd.DataFrame:
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    permits = pull_census("permits")
    starts = pull_census("starts")
    panel = pd.DataFrame(
        {
            "sales": pull_nrs(SOLD_URL),
            "forsale": pull_nrs(FSALE_URL),
            "sf_permits": permits["sf"],
            "sf_starts": starts["sf"],
        }
    )
    panel = panel.join(pull_hmi_components(), how="outer")
    monthly_mort = pull_pmms().resample("MS").mean()
    panel = panel.join(monthly_mort.rename("mortgage"), how="outer")
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    panel.index.name = "month"

    if cache is not None:
        panel.to_csv(cache)
    return panel


# Production BigQuery pulls (the collectors for every input are deployed).


def pull_sales_bq(client=None) -> pd.DataFrame:
    """Sales + for-sale (SA, US total) from BigQuery, latest vintage."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT series, observation_month, value
    FROM `{PROJECT}.census_construction.new_residential_sales`
    WHERE segment = 'total' AND seasonally_adjusted
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY series, observation_month ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="series", values="value")
    return wide.rename(columns={"sold": "sales", "for_sale": "forsale"})


def pull_sf_construction_bq(client=None) -> pd.DataFrame:
    """SF permits + starts (SAAR) from BigQuery, latest vintage."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT series, observation_month, value
    FROM `{PROJECT}.census_construction.new_residential_construction`
    WHERE segment = 'single_family' AND seasonally_adjusted
      AND series IN ('permits', 'starts')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY series, observation_month ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="series", values="value")
    return wide.rename(columns={"permits": "sf_permits", "starts": "sf_starts"})


def pull_hmi_bq(client=None) -> pd.DataFrame:
    """HMI + SF-sales-present from BigQuery, latest vintage."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT measure, observation_month, value
    FROM `{PROJECT}.nahb_hmi.housing_market_index`
    WHERE measure IN ('hmi', 'sf_sales_present')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY measure, observation_month ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    return frame.pivot(index="observation_month", columns="measure", values="value")
