"""Shared model code for the headline-PPI y/y forecast (harness + production).

The forecastable object is the month-M NSA m/m change (``y`` = Delta-log
``WPUFD4``); the published y/y follows by arithmetic because the 12-month base
is known at the origin:

    yy_M = I_{M-1} * exp(yhat) / I_{M-12} - 1

PPI's m/m is dominated by three buckets: energy goods (fast, observable --
prices reference the mid-month pricing date, which the daily spots cover),
foods (lagged pass-through), and trade-services margins (large idiosyncratic
noise, essentially unforecastable at h=1). Survey prices (ISM prices paid,
month M published before the PPI release) proxy the breadth of producer price
pressure. The bake-off (harness.py) arbitrates; SPEC_COLS holds the winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (2017-2026 COVID-masked: y/y RMSE 0.29 vs 0.58 rw_yy, -50%;
# 2022+ subwindow 0.32 vs 0.71; ~86% direction on the y/y change): gasoline +
# diesel changes dated to the PPI pricing date, month-M ISM mfg prices paid,
# expanding seasonal mean, SA lag 1. Ablations: Henry Hub, trade-services lag,
# imports, and extra lags each add nothing (see harness/README leaderboard).
SPEC_COLS: list[str] = ["dgas_mid_0", "ddiesel_mid_0", "ismm_0", "seas", "dsa_1"]
MIN_TRAIN = 72  # months; FD-ID history only begins 2009-11


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from the monthly inputs (research specs skip absent
    columns; production panels carry only the winner's inputs)."""
    p = pd.DataFrame(index=panel.index)
    p["nsa_idx"] = panel["nsa_idx"]
    p["y"] = np.log(panel["nsa_idx"]).diff()  # target: month-M NSA m/m
    p["y12"] = p["y"].shift(12)  # the known base-effect term

    # Own persistence, published through M-1.
    p["dnsa_1"] = p["y"].shift(1)
    p["dnsa_2"] = p["y"].shift(2)
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    if "sa_idx" in panel:
        dsa = np.log(panel["sa_idx"]).diff()
        p["dsa_1"] = dsa.shift(1)
        p["dsa_2"] = dsa.shift(2)

    # FD-ID component m/m (SA), lag 1: heterogeneous persistence -- energy
    # transitory, services sticky, trade margins noise.
    for name in ("energy", "foods", "core_goods", "trade", "svc_xtrade"):
        if name in panel:
            p[f"d{name}_1"] = np.log(panel[name]).diff().shift(1)

    # ISM prices paid: month M released the 1st/3rd business day of M+1,
    # before the PPI print -- lag 0 legal. Centered at the 50 breakeven.
    for col, short in (("ism_mfg", "ismm"), ("ism_svc", "isms")):
        if col in panel:
            level = (panel[col] - 50.0) / 100.0
            p[f"{short}_0"] = level
            p[f"{short}_1"] = level.shift(1)
            p[f"d{short}_0"] = level.diff()

    # Energy spots at the PPI pricing date (Tuesday of the week containing the
    # 13th); month M observed in full at the origin -- lag 0 legal. The _avg
    # variants are the CPI-style complete-month means, kept as comparators.
    for col in ("gas_mid", "wti_mid", "diesel_mid", "hh_mid", "gas_avg", "wti_avg"):
        if col in panel:
            dlog = np.log(panel[col]).diff()
            p[f"d{col}_0"] = dlog
            p[f"d{col}_1"] = dlog.shift(1)

    # Import prices: month M releases after the PPI -- lag 1 only.
    if "imports" in panel:
        p["dimp_1"] = np.log(panel["imports"]).diff().shift(1)
    return p


@dataclass(frozen=True)
class PpiForecast:
    target_month: pd.Timestamp
    level: float  # NSA index points
    mm_pct: float  # m/m percent change (NSA)
    yy_pct: float  # y/y percent change (the headline figure)
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> PpiForecast | None:
    """Fit the spec on all published history and forecast the next PPI month.

    Returns None when the joint history is too short, the 12-month base is
    missing, or any regressor for the target month is unavailable.
    """
    cols = cols or SPEC_COLS
    last_ppi = panel["nsa_idx"].last_valid_index()
    if last_ppi is None:
        return None
    target = last_ppi + pd.offsets.MonthBegin(1)

    extended = panel.reindex(panel.index.union([target]))
    p = build_features(extended)

    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    i = p.index.get_loc(target)
    base_month = target - pd.offsets.MonthBegin(12)
    if base_month not in panel.index or not np.isfinite(panel.at[base_month, "nsa_idx"]):
        return None
    if not np.isfinite(X[i]).all():
        return None
    train = np.isfinite(X).all(axis=1) & np.isfinite(y) & (np.arange(len(p)) < i)
    if train.sum() < MIN_TRAIN:
        return None

    beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    yhat = float(X[i] @ beta)
    last_level = float(panel.at[last_ppi, "nsa_idx"])
    base_level = float(panel.at[base_month, "nsa_idx"])
    level = last_level * float(np.exp(yhat))
    return PpiForecast(
        target_month=target,
        level=level,
        mm_pct=float(np.expm1(yhat) * 100),
        yy_pct=(level / base_level - 1.0) * 100,
        n_train=int(train.sum()),
    )
