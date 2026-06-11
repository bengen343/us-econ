"""Shared model code for the ISM Manufacturing PMI forecast.

The forecastable object is the month-M CHANGE in the headline PMI; the level
is recovered as pmi_{M-1} + yhat. Survey levels enter as gaps to the prior
ISM print (survey_M - pmi_{M-1}) -- the natural "where the month-M surveys
say the ISM should land" transform -- plus own-history terms. The bake-off
(harness.py) arbitrates; SPEC_COLS holds the winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (flash-era window 2018+ COVID-masked: change RMSE 1.07 vs
# 1.20 random walk, -11%; MAE 0.85): the month-M S&P flash gap + the
# equal-weight Fed-survey composite gap + the own lag. On the long window
# the Fed composite alone (fed4: 1.25 vs 1.32 rw) is the best deep-history
# spec. Chicago PMI HURT recently (1.40-1.41) -- conveniently, since its
# source is subscriber-gated; new-orders lead, mean reversion, and LightGBM
# all lost (see harness/README leaderboards).
SPEC_COLS: list[str] = ["fl_gap", "fed_gap", "dpmi_1"]
MIN_TRAIN = 96
MIN_TRAIN_SHORT = 60  # flash-era specs (S&P flash only starts 2012)


def build_features(
    panel: pd.DataFrame, extra_months: list[pd.Timestamp] | None = None
) -> pd.DataFrame:
    index = panel.index
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)
    p["pmi"] = panel["pmi"].reindex(index)
    p["y"] = p["pmi"].diff()

    # Own history, published through M-1.
    p["dpmi_1"] = p["y"].shift(1)
    p["dpmi_2"] = p["y"].shift(2)
    p["pmi50_1"] = (p["pmi"] - 50.0).shift(1)  # mean reversion toward breakeven
    if "new_orders" in panel:
        p["orders_gap_1"] = (panel["new_orders"] - panel["pmi"]).shift(1).reindex(index)

    # Month-M surveys (all published before the origin), as gaps to the prior
    # ISM print and as own changes.
    for col, short in (
        ("chicago", "chi"),
        ("empire", "emp"),
        ("philly", "phl"),
        ("richmond", "ric"),
        ("dallas", "dal"),
        ("flash_mfg", "fl"),
    ):
        if col in panel:
            level = panel[col].reindex(index)
            p[f"{short}_gap"] = level - p["pmi"].shift(1)
            p[f"d{short}"] = level.diff()

    # Equal-weight composite of the four Fed surveys (ISM scale).
    fed = [c for c in ("empire", "philly", "richmond", "dallas") if c in panel]
    if fed:
        composite = panel[fed].reindex(index).mean(axis=1)
        p["fed_gap"] = composite - p["pmi"].shift(1)
    return p


@dataclass(frozen=True)
class IsmForecast:
    target_month: pd.Timestamp
    level: float  # PMI points
    change: float  # vs the prior print
    n_train: int


def forecast_next(
    panel: pd.DataFrame, cols: list[str] | None = None, min_train: int | None = None
) -> IsmForecast | None:
    """Fit the spec on all published history and forecast the next ISM month.

    Returns None when any month-M survey in the spec is unavailable -- right
    after an ISM release the target rolls forward and the job idles until
    the next month's surveys land (mid-to-late month)."""
    cols = cols or SPEC_COLS
    min_train = min_train or (
        MIN_TRAIN_SHORT if any(c.startswith("fl") for c in cols) else MIN_TRAIN
    )
    last = panel["pmi"].last_valid_index()
    if last is None:
        return None
    target = last + pd.offsets.MonthBegin(1)

    p = build_features(panel, extra_months=[target])
    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    i = p.index.get_loc(target)
    if not np.isfinite(X[i]).all():
        return None
    train = np.isfinite(X).all(axis=1) & np.isfinite(y) & (np.arange(len(p)) < i)
    if train.sum() < min_train:
        return None

    beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    yhat = float(X[i] @ beta)
    anchor = float(panel.at[last, "pmi"])
    return IsmForecast(
        target_month=target,
        level=anchor + yhat,
        change=yhat,
        n_train=int(train.sum()),
    )
