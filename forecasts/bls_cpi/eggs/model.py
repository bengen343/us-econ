"""Shared model code for the egg-price forecast (research harness + production).

The production spec is ``ppi_dl3_ar_seas`` from the bake-off (see harness.py):
OLS of the month-M Delta-log average egg price on

  [1, Delta-log AP_{M-1}, Delta-log PPI_{M-1..M-3}, expanding seasonal mean]

-- an AR(1) + wholesale distributed lag + NSA seasonality. Wholesale (PPI
chicken eggs) enters at lags 1-3 because retail follows wholesale with a 2-5
week lag and the M-1 PPI print is the latest published at the forecast origin.
On the 2010-2026 COVID-masked backtest it beats the random walk by ~18% m/m
RMSE ($0.125 vs $0.150 level MAE) with 66% directional accuracy; ECM gap and
asymmetric terms added nothing on top of the three lags, and SARIMA / LightGBM
trailed it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SPEC_COLS = ["dap_1", "dppi_1", "dppi_2", "dppi_3", "seas"]
MIN_TRAIN = 96  # months of joint history required before forecasting


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from monthly ``ap`` ($/dozen) and ``ppi`` columns.

    Every regressor is dated so the month-M row uses only information published
    at the origin (just before M's mid-(M+1) release): AP through M-1, PPI
    through M-1.
    """
    p = pd.DataFrame(index=panel.index)
    p["ap"] = panel["ap"]
    p["y"] = np.log(panel["ap"]).diff()  # target: Delta-log AP, month M

    p["dap_1"] = p["y"].shift(1)
    dppi = np.log(panel["ppi"]).diff()
    p["dppi_1"] = dppi.shift(1)
    p["dppi_2"] = dppi.shift(2)
    p["dppi_3"] = dppi.shift(3)
    p["dppi_1_pos"] = p["dppi_1"].clip(lower=0.0)
    p["dppi_1_neg"] = p["dppi_1"].clip(upper=0.0)
    p["dppi_2_pos"] = p["dppi_2"].clip(lower=0.0)
    p["dppi_2_neg"] = p["dppi_2"].clip(upper=0.0)
    # ECM gap: log retail/wholesale margin as of M-1 (beta=1 imposed; the OLS
    # constant absorbs the level of the long-run margin). Research-only.
    p["gap_1"] = (np.log(panel["ap"]) - np.log(panel["ppi"])).shift(1)
    # Expanding calendar-month mean of the target, prior months only (PIT).
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    return p


@dataclass(frozen=True)
class EggsForecast:
    target_month: pd.Timestamp
    level: float  # $/dozen
    mm_pct: float  # m/m percent change
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> EggsForecast | None:
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
    return EggsForecast(
        target_month=target,
        level=last_level * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
