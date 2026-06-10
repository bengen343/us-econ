"""Data pulls for the CPI-shelter forecast (research harness + production).

The target is the CPI shelter index, ``CUSR0000SAH1`` (SA -- the published m/m
comes from the SA series). Candidate inputs:

  * Own sub-components, OER (``SEHC``, ~3/4 of shelter) and rent of primary
    residence (``SEHA``) -- published WITH the target, so they enter lagged.
  * ZORI, the Zillow market-rent index (national, SA, from 2015) -- the
    literature's market-rent channel. Documented lead on CPI shelter is 8-14
    months (Richmond Fed; NBER w34113; Boston Fed uses the 6-month lag), so it
    is a candidate even at h=1 only through its slow-moving trend. ZORI for
    month M-1 is published ~2-3 weeks after month end, before the mid-(M+1)
    CPI release -- lags >= 1 are PIT-clean.
  * The BLS New Tenant Rent index was considered and excluded: quarterly, lead
    of 6-12+ months, and publication is paused (2026-04) so production could
    not consume it anyway.

The harness fetches CPI series from the BLS API (full history; BigQuery has
2006+); production reads BigQuery (``bls_cpi.cpi_series`` + ``zillow_rent.zori``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

CPI_SHELTER_SA = "CUSR0000SAH1"
CPI_OER_SA = "CUSR0000SEHC"
CPI_RENT_SA = "CUSR0000SEHA"

_START_YEAR = 1985  # OER (SEHC) starts 1983; shelter/rent are older


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_zori_sa(client=None) -> pd.Series:
    """ZORI national SA level, latest vintage per month, from BigQuery."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project="us-econ-51920")
    sql = """
    WITH ranked AS (
      SELECT observation_date, value,
             ROW_NUMBER() OVER (PARTITION BY observation_date
                                ORDER BY ingested_at DESC) AS rn
      FROM `us-econ-51920.zillow_rent.zori`
      WHERE seasonally_adjusted = TRUE
    )
    SELECT observation_date, value FROM ranked WHERE rn = 1
    ORDER BY observation_date
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel: ``sh_idx`` (shelter SA), ``oer_idx``, ``rent_idx``, ``zori``."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    by_series = fetch_series(
        [CPI_SHELTER_SA, CPI_OER_SA, CPI_RENT_SA],
        _START_YEAR,
        date.today().year,
        api_key=api_key,
    )
    panel = pd.DataFrame(
        {
            "sh_idx": _points_to_series(by_series[CPI_SHELTER_SA]),
            "oer_idx": _points_to_series(by_series[CPI_OER_SA]),
            "rent_idx": _points_to_series(by_series[CPI_RENT_SA]),
            "zori": pull_zori_sa(),
        }
    ).sort_index()
    panel.index.name = "month"

    if cache is not None:
        panel.to_csv(cache)
    return panel


def maybe_api_key() -> str | None:
    """Best-effort BLS API key from Secret Manager (higher quota); None offline."""
    try:
        from collectors.common.secrets import get_secret

        return get_secret("us-econ-51920", "bls-api-key")
    except Exception:
        return None
