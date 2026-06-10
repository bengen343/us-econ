"""Data pulls for the airline-fares forecast (research harness + production).

The target is the CPI airline fares index, ``CUSR0000SETG01`` (SA -- the
published m/m comes from the SA series). Candidate inputs:

  * PPI scheduled passenger air transportation -- industry
    ``PCU481111481111`` and commodity ``WPU3022``. A producer-side fare
    measure (average-pricing methodology since ~2009). The M-1 print is
    published mid-M, before the mid-(M+1) CPI release: lags >= 1 are
    PIT-clean (same timing as the eggs/electricity PPI regressors).
  * Jet fuel (Gulf Coast kerosene-type spot, FRED ``WJFUELUSGULF`` weekly,
    aggregated to complete-month means) and WTI crude (daily, already in
    ``eia_petroleum.prices``). Fuel pass-through to fares unfolds over 1-4
    QUARTERS (hedging, capacity planning), so fuel enters through lags and
    trailing means rather than the contemporaneous month.

The harness fetches BLS series from the API and jet fuel from FRED's public
CSV; production reads BigQuery only (which inputs depend on the winning spec).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

CPI_AIR_SA = "CUSR0000SETG01"
CPI_AIR_NSA = "CUUR0000SETG01"
PPI_AIR_IND = "PCU481111481111"  # PPI industry: scheduled passenger air transport
PPI_AIR_COM = "WPU3022"  # PPI commodity: airline passenger services
FRED_JET_FUEL = "WJFUELUSGULF"  # Gulf Coast jet fuel spot, weekly, $/gal
EIA_WTI = "RWTC"  # WTI spot, daily, in eia_petroleum.prices

_START_YEAR = 1989  # CPI airline fares starts 1989


def monthly_mean_complete(obs: pd.Series, max_gap_days: int = 13) -> pd.Series:
    """Calendar-month means of a daily/weekly series, only for months whose
    observations span the month (first obs in the first ``max_gap_days`` days,
    last obs within ``max_gap_days`` of month end). Mirrors the gasoline
    forecast's complete-month guard; the wider default gap accommodates the
    weekly (Friday) jet-fuel cadence."""
    frame = obs.dropna().to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    grouped = frame.groupby("month")["value"]
    first_day = frame.groupby("month").apply(lambda g: g.index.min().day)
    last_gap = frame.groupby("month").apply(
        lambda g: (g.index.max().to_period("M").to_timestamp("M") - g.index.max()).days
    )
    complete = (first_day <= max_gap_days) & (last_gap <= max_gap_days - 1)
    return grouped.mean()[complete]


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_jet_fuel() -> pd.Series:
    """Gulf Coast jet fuel weekly spot from FRED's public (keyless) CSV."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_JET_FUEL}"
    frame = pd.read_csv(url, parse_dates=["observation_date"], na_values=".")
    return pd.Series(
        frame[FRED_JET_FUEL].to_numpy(dtype=float), index=frame["observation_date"]
    ).sort_index()


def pull_wti_daily(client=None) -> pd.Series:
    """WTI daily spot from BigQuery (clean upserted table)."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project="us-econ-51920")
    sql = """
    SELECT observation_date, value
    FROM `us-econ-51920.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", EIA_WTI)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel: ``sa_idx``, ``nsa_idx`` (CPI airfares), ``ppi_ind``,
    ``ppi_com``, ``jet`` ($/gal), ``wti`` ($/bbl)."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    by_series = fetch_series(
        [CPI_AIR_SA, CPI_AIR_NSA, PPI_AIR_IND, PPI_AIR_COM],
        _START_YEAR,
        date.today().year,
        api_key=api_key,
    )
    panel = pd.DataFrame(
        {
            "sa_idx": _points_to_series(by_series[CPI_AIR_SA]),
            "nsa_idx": _points_to_series(by_series[CPI_AIR_NSA]),
            "ppi_ind": _points_to_series(by_series[PPI_AIR_IND]),
            "ppi_com": _points_to_series(by_series[PPI_AIR_COM]),
            "jet": monthly_mean_complete(pull_jet_fuel()),
            "wti": monthly_mean_complete(pull_wti_daily()),
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
