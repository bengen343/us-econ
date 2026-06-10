"""Shared model code for the CPI-gasoline forecast (research harness + production).

Forecasts Delta-log of the SA gasoline index for the about-to-be-released
month M from the month's own (fully published) retail price change. Feature
dating: at the origin -- just before the mid-(M+1) CPI release -- the CPI is
published through M-1 and the EIA weekly retail series covers month M
completely, so ``eia_mm`` enters contemporaneously.

``wedge`` is the expanding calendar-month mean of (y - eia_mm), prior years
only: the SA target differs from the NSA-ish retail change by the BLS seasonal
factor (plus a sampling wedge), which is stable by calendar month.

The production spec is the bake-off winner from harness.py: OLS on
[1, eia_mm, wedge]. On 2010-2026 COVID-masked origins it cuts the random
walk's m/m RMSE by ~65% (1.55 vs 4.46; level MAE 3.5 index points, 87%
directional); adding the lag-1 retail change or an AR term only hurt, and the
fitted form edged out the deterministic dms pass-through (RMSE 1.56) used for
the CPI-headline energy component.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SPEC_COLS = ["eia_mm", "wedge"]
MIN_TRAIN = 96  # months of joint history required before forecasting


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from monthly ``sa_idx``, ``nsa_idx``, ``eia`` columns."""
    p = pd.DataFrame(index=panel.index)
    p["sa_idx"] = panel["sa_idx"]
    p["y"] = np.log(panel["sa_idx"]).diff()  # target: Delta-log SA index, month M

    p["dgas_1"] = p["y"].shift(1)
    p["eia_mm"] = np.log(panel["eia"]).diff()  # month M retail change (published)
    p["eia_mm_1"] = p["eia_mm"].shift(1)
    # Calendar-month wedge between the SA target and the raw retail change
    # (the seasonal factor, essentially), expanding over prior years (PIT).
    p["wedge"] = (
        (p["y"] - p["eia_mm"])
        .groupby(p.index.month)
        .transform(lambda s: s.shift(1).expanding().mean())
    )
    # NSA twin of the wedge, for the dms-style baseline in the harness.
    if "nsa_idx" in panel:
        y_nsa = np.log(panel["nsa_idx"]).diff()
        p["y_nsa"] = y_nsa
        p["sa_gap"] = (
            (y_nsa - p["y"])
            .groupby(p.index.month)
            .transform(lambda s: s.shift(1).expanding().mean())
        )
    # Expanding calendar-month mean of the target itself (PIT).
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    return p


@dataclass(frozen=True)
class GasForecast:
    target_month: pd.Timestamp
    level: float  # SA index points
    mm_pct: float  # m/m percent change (SA)
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> GasForecast | None:
    """Fit the spec on all published history and forecast the next CPI month.

    The target month is the first month after the last published SA index.
    Returns None when the joint history is too short or any regressor for the
    target month is unavailable (in particular ``eia_mm``, which data.py blanks
    until the target month's weekly retail prices are complete).
    """
    cols = cols or SPEC_COLS
    last_cpi = panel["sa_idx"].last_valid_index()
    if last_cpi is None:
        return None
    target = last_cpi + pd.offsets.MonthBegin(1)

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
    last_level = float(panel.at[last_cpi, "sa_idx"])
    return GasForecast(
        target_month=target,
        level=last_level * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
