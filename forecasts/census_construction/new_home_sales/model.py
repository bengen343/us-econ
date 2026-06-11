"""Shared model code for the new-home-sales forecast.

The target is Delta-log of the SAAR for month M, with levels recovered as
sales_{M-1} * exp(yhat). Same-month SF permits enter both as a change and as
a level gap (the sales sample is permit-drawn); HMI/mortgage/supply cover
demand-side state. The bake-off (harness.py) arbitrates; SPEC_COLS holds the
winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (2010-2026 COVID-masked: m/m RMSE 6.93 vs 8.03
# carry-forward, -14%; 7.03 vs 7.97 on 2017+; best direction 69-72%):
# same-month SF permits as both a change and a level gap to prior sales --
# the sales sample is permit-drawn -- plus the mean-reverting own lag. The
# kitchen sink (+mortgage/supply/HMI) shaved ~0.2 RMSE but needs a new
# Freddie-PMMS collector and risks overfit on the noisiest housing series;
# HMI variants, mortgage, supply, starts, and LightGBM all added little
# alone (see harness/README leaderboard).
SPEC_COLS: list[str] = ["dperm_0", "perm_gap", "dnhs_1"]
MIN_TRAIN = 120


def build_features(
    panel: pd.DataFrame, extra_months: list[pd.Timestamp] | None = None
) -> pd.DataFrame:
    index = panel.index
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)
    p["sales"] = panel["sales"].reindex(index)
    p["y"] = np.log(p["sales"]).diff()

    # Own history (heavy mean reversion expected).
    p["dnhs_1"] = p["y"].shift(1)
    p["dnhs_2"] = p["y"].shift(2)

    # Same-month SF construction (NRC releases ~a week before this print).
    for col, short in (("sf_permits", "perm"), ("sf_starts", "start")):
        if col in panel:
            level = np.log(panel[col]).reindex(index)
            p[f"d{short}_0"] = level.diff()
            p[f"{short}_gap"] = level - np.log(p["sales"]).shift(1)

    # Builder survey: month M, plus the M+1 print that is also out before the
    # release (~16th of M+1) -- a true leading variant.
    if "hmi" in panel:
        hmi = panel["hmi"].reindex(index)
        p["dhmi_0"] = hmi.diff()
        p["dhmi_lead"] = hmi.diff().shift(-1)
    if "sf_sales_present" in panel:
        present = panel["sf_sales_present"].reindex(index)
        p["dhmip_0"] = present.diff()
        p["dhmip_lead"] = present.diff().shift(-1)

    # Affordability (month-M mean of the weekly 30-yr rate).
    if "mortgage" in panel:
        mort = panel["mortgage"].reindex(index)
        p["dmort_0"] = mort.diff()
        p["dmort_t3"] = mort.diff(3)

    # Inventory state: months' supply at the end of M-1.
    if "forsale" in panel:
        supply = (panel["forsale"] / (panel["sales"] / 12.0)).reindex(index)
        p["supply_1"] = supply.shift(1)
        p["dsupply_1"] = supply.diff().shift(1)
    return p


@dataclass(frozen=True)
class SalesForecast:
    target_month: pd.Timestamp
    level: float  # SAAR, thousands
    mm_pct: float
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> SalesForecast | None:
    """Fit the spec on all published history and forecast the next sales
    month. Returns None when the target month's regressors are unavailable
    (before the same-month NRC release lands ~the 17th)."""
    cols = cols or SPEC_COLS
    last = panel["sales"].last_valid_index()
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
    if train.sum() < MIN_TRAIN:
        return None

    beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    yhat = float(X[i] @ beta)
    anchor = float(panel.at[last, "sales"])
    return SalesForecast(
        target_month=target,
        level=anchor * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
