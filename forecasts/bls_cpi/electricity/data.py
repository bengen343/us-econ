"""Data pulls for the electricity-price forecast (research harness + production).

The target is the BLS average price of electricity, ``APU000072610`` ($/kWh,
NSA -- the famous FRED series). Candidate inputs:

  * PPI electric power (``WPU0541`` residential, ``WPU054`` all sectors) --
    the producer-side price. PPI for M-1 is published mid-M, before the
    mid-(M+1) CPI/AP release, so lags >= 1 are PIT-clean (same timing as the
    eggs forecast's PPI regressor).
  * Henry Hub natural gas (FRED ``MHHNGSP``, monthly mean of the daily spot)
    -- the marginal generation fuel. Spot data, so month M is fully published
    at the origin (lag 0 usable). The literature expects little at h=1:
    fuel-cost pass-through is gated by utility rate cases (months-to-years
    lags) and has structurally diverged from gas since ~2023 (grid costs).

Retail electricity is an administered price: strong NSA seasonality (summer
rate schedules) + persistence are expected to dominate; the exogenous inputs
have to earn their place in the bake-off.

The harness fetches BLS series from the API (full history; BigQuery has 2006+)
and Henry Hub from FRED's public CSV endpoint; production reads BigQuery only.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

AP_ELECTRICITY = "APU000072610"  # avg price: electricity, $/kWh (NSA)
PPI_ELEC_RES = "WPU0541"  # PPI commodity: residential electric power (NSA)
PPI_ELEC_ALL = "WPU054"  # PPI commodity: electric power, all sectors (NSA)
FRED_HENRY_HUB = "MHHNGSP"  # Henry Hub natural gas spot, monthly mean, $/MMBtu

_START_YEAR = 1980


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_henry_hub() -> pd.Series:
    """Henry Hub monthly spot from FRED's public (keyless) CSV endpoint."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={FRED_HENRY_HUB}"
    frame = pd.read_csv(url, parse_dates=["observation_date"], na_values=".")
    return pd.Series(
        frame[FRED_HENRY_HUB].to_numpy(dtype=float),
        index=frame["observation_date"],
    ).sort_index()


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel: ``ap`` ($/kWh), ``ppi_res``, ``ppi_all``, ``hh``."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    by_series = fetch_series(
        [AP_ELECTRICITY, PPI_ELEC_RES, PPI_ELEC_ALL],
        _START_YEAR,
        date.today().year,
        api_key=api_key,
    )
    panel = pd.DataFrame(
        {
            "ap": _points_to_series(by_series[AP_ELECTRICITY]),
            "ppi_res": _points_to_series(by_series[PPI_ELEC_RES]),
            "ppi_all": _points_to_series(by_series[PPI_ELEC_ALL]),
            "hh": pull_henry_hub(),
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
