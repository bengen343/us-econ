"""Data pulls for the egg-price forecast research harness.

Research-only and read-only. Unlike the other harnesses this one fetches from
the BLS API directly rather than BigQuery: the target (average price of eggs,
``APU0000708111``) is in ``bls_cpi.average_prices`` only from 2006, and the
wholesale regressor (PPI chicken eggs, ``WPU017107``) is not collected yet --
the API gives the full joint history 1992+ in one place. The production job
reads the same two series from BigQuery instead (``bls_cpi.average_prices`` +
``bls_ppi.ppi_series`` once the commodity series is collected).

Timing (why the PPI enters lagged): the CPI/AP print for month M lands ~the
10th-15th of M+1; the PPI for month M lands a few days AFTER that. So at the
forecast origin (just before the AP release for M) the latest published PPI is
M-1. Retail egg prices follow wholesale with a ~2-5 week lag (USDA ERS;
farmdoc), so the M-1/M-2 wholesale changes are exactly the information that
month M's retail print responds to.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

AP_EGGS = "APU0000708111"  # avg price: eggs, grade A, large, $/dozen (NSA)
PPI_EGGS = "WPU017107"  # PPI commodity: chicken eggs (NSA), from 1991-12

_START_YEAR = 1980  # AP eggs starts 1980-01; PPI joins 1991-12


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel with columns ``ap`` ($/dozen) and ``ppi`` (index level).

    ``cache`` (a CSV path) avoids re-hitting the BLS API on every harness
    iteration -- the anonymous quota is 25 requests/day.
    """
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    by_series = fetch_series([AP_EGGS, PPI_EGGS], _START_YEAR, date.today().year, api_key=api_key)
    panel = pd.DataFrame(
        {
            "ap": _points_to_series(by_series[AP_EGGS]),
            "ppi": _points_to_series(by_series[PPI_EGGS]),
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
