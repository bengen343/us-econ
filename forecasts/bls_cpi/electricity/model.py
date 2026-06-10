"""Shared model code for the electricity-price forecast (research harness +
production).

Retail electricity is an administered price: utility rates move through rate
cases, so persistence + the strong NSA seasonal pattern (summer rate
schedules) carry most of the one-month-ahead signal; producer-side prices
(PPI electric power) pass through slowly and fuel costs (Henry Hub) barely
at all at this horizon. The bake-off (harness.py) decides the spec; SPEC_COLS
holds the winner.

Feature dating: at the origin (just before the mid-(M+1) CPI/AP release) the
AP is published through M-1, PPI through M-1, and Henry Hub (spot) through M.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (2010-2026 COVID-masked: m/m RMSE 0.95 vs 1.66 random walk):
# pure own-history -- AR(1) + year-ago m/m + expanding calendar-month mean.
# Every PPI-electric-power and Henry Hub spec scored worse; administered
# retail rates carry no exploitable producer/fuel signal at h=1.
SPEC_COLS: list[str] = ["dap_1", "dap_12", "seas"]
MIN_TRAIN = 96  # months of joint history required before forecasting


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from monthly ``ap`` ($/kWh) plus optional ``ppi_res``,
    ``ppi_all``, ``hh`` columns (research specs skip absent inputs)."""
    p = pd.DataFrame(index=panel.index)
    p["ap"] = panel["ap"]
    p["y"] = np.log(panel["ap"]).diff()  # target: Delta-log AP, month M

    # Own persistence + NSA seasonality (published through M-1).
    p["dap_1"] = p["y"].shift(1)
    p["dap_12"] = p["y"].shift(12)
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())

    # Producer-side electric power, lags >= 1 (the M-1 PPI print is published
    # mid-M, before the origin).
    if "ppi_res" in panel:
        dppir = np.log(panel["ppi_res"]).diff()
        p["dppir_1"] = dppir.shift(1)
        p["dppir_2"] = dppir.shift(2)
        p["dppir_3"] = dppir.shift(3)
        p["ppir_trail12"] = dppir.shift(1).rolling(12).mean()
    if "ppi_all" in panel:
        dppia = np.log(panel["ppi_all"]).diff()
        p["dppia_1"] = dppia.shift(1)
        p["dppia_2"] = dppia.shift(2)
        p["dppia_3"] = dppia.shift(3)

    # Fuel costs (Henry Hub spot), lag 0 usable -- market data, month M is
    # complete at the origin. Research-only unless the bake-off says otherwise.
    if "hh" in panel:
        dhh = np.log(panel["hh"]).diff()
        p["dhh_0"] = dhh
        p["dhh_1"] = dhh.shift(1)
        p["dhh_6"] = dhh.shift(6)
        p["dhh_12"] = dhh.shift(12)
        p["hh_trail12"] = dhh.shift(1).rolling(12).mean()
    return p


@dataclass(frozen=True)
class ElectricityForecast:
    target_month: pd.Timestamp
    level: float  # $/kWh
    mm_pct: float  # m/m percent change
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> ElectricityForecast | None:
    """Fit the spec on all published history and forecast the next AP month.

    The target month is the first month after the last published AP print.
    Returns None when the joint history is too short or any regressor for the
    target month is unavailable (e.g. the M-1 PPI print is missing).
    """
    cols = cols or SPEC_COLS
    last_ap = panel["ap"].last_valid_index()
    if last_ap is None:
        return None
    target = last_ap + pd.offsets.MonthBegin(1)

    extended = panel.reindex(panel.index.union([target]))
    p = build_features(extended)

    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    i = p.index.get_loc(target)
    if not np.isfinite(X[i]).all():
        return None
    train = np.isfinite(X).all(axis=1) & np.isfinite(y) & (np.arange(len(p)) < i)
    if train.sum() < MIN_TRAIN:
        return None

    beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    yhat = float(X[i] @ beta)
    last_level = float(panel.at[last_ap, "ap"])
    return ElectricityForecast(
        target_month=target,
        level=last_level * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
