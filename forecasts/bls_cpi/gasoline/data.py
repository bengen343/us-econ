"""Data pulls for the CPI-gasoline forecast (research harness + production).

The target is the CPI gasoline (all types) index, ``CUSR0000SETB01`` (SA --
the published m/m comes from the SA series; the NSA twin feeds the dms-style
baseline in the harness). The regressor is the EIA weekly U.S. retail price of
gasoline (all grades), aggregated to a calendar-month mean: BLS computes the
gasoline index from daily pump prices over the full calendar month, so the
month-M retail mean is (nearly) the index's own sampling frame and is fully
published BEFORE the mid-(M+1) CPI release -- the regressor is contemporaneous,
not lagged, unlike the eggs forecast.

The harness fetches the CPI series from the BLS API (full history to pair with
EIA's 2000+ weekly data; BigQuery has 2006+); production reads BigQuery
(``bls_cpi.cpi_series`` + ``eia_petroleum.prices``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

CPI_GAS_SA = "CUSR0000SETB01"
CPI_GAS_NSA = "CUUR0000SETB01"
EIA_GAS_WEEKLY = "EMM_EPM0_PTE_NUS_DPG"  # all-grades retail, $/gal, Mondays

_START_YEAR = 1995  # EIA weekly starts 2000; a little CPI run-up for features


def monthly_mean_complete(weekly: pd.Series) -> pd.Series:
    """Calendar-month means of the weekly series, only for COMPLETE months.

    A month counts as complete when its weekly observations span it end to end
    (first obs within the first 7 days, last obs within the last 7 -- the
    Monday cadence guarantees both once the month is fully published). The CPI
    samples every day of the month, so a partial-month mean is biased toward
    whichever half is observed; incomplete months are dropped (NaN), which is
    also the production guard against forecasting from a half-published month.
    """
    frame = weekly.dropna().to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    grouped = frame.groupby("month")["value"]
    first_day = frame.groupby("month").apply(lambda g: g.index.min().day)
    last_gap = frame.groupby("month").apply(
        lambda g: (g.index.max().to_period("M").to_timestamp("M") - g.index.max()).days
    )
    complete = (first_day <= 7) & (last_gap <= 6)
    return grouped.mean()[complete]


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel: ``sa_idx``, ``nsa_idx`` (CPI gasoline) and ``eia``
    ($/gal, complete-month mean of the weekly retail price)."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    by_series = fetch_series(
        [CPI_GAS_SA, CPI_GAS_NSA], _START_YEAR, date.today().year, api_key=api_key
    )

    from google.cloud import bigquery

    client = bigquery.Client(project="us-econ-51920")
    sql = """
    SELECT observation_date, value
    FROM `us-econ-51920.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", EIA_GAS_WEEKLY)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    weekly = pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )

    panel = pd.DataFrame(
        {
            "sa_idx": _points_to_series(by_series[CPI_GAS_SA]),
            "nsa_idx": _points_to_series(by_series[CPI_GAS_NSA]),
            "eia": monthly_mean_complete(weekly),
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
