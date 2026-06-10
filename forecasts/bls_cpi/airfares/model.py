"""Shared model code for the airline-fares forecast (research harness +
production).

Airline fares are the most volatile CPI services component: prices move with
fuel costs (slowly -- pass-through unfolds over 1-4 quarters because of
hedging and capacity planning), demand, and capacity. The bake-off
(harness.py) arbitrates between own-history persistence, the producer-side
PPI airline fare measures (lag 1), and fuel-cost lags; SPEC_COLS holds the
winner.

Feature dating: at the origin (just before the mid-(M+1) CPI release) the CPI
is published through M-1, PPI through M-1, and the fuel spots through M.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (2010-2026 COVID-masked: m/m RMSE 2.02 vs 2.60 carry-forward
# / 2.67 zero; robust on the 2017+ window): AR(2) -- airfares m/m mean-reverts
# -- plus WTI changes at lags 0-2. The contemporaneous month's complete WTI
# mean is published before the origin (daily spot), same timing as the
# gasoline forecast's retail regressor. PPI airline specs (industry +
# commodity) and jet-fuel variants all scored worse; WTI's daily cadence beat
# the weekly jet-fuel series despite being one step further from airline costs.
SPEC_COLS: list[str] = ["dap_1", "dap_2", "dwti_0", "dwti_1", "dwti_2"]
MIN_TRAIN = 96  # months of joint history required before forecasting


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from monthly ``sa_idx`` plus optional ``ppi_ind``,
    ``ppi_com``, ``jet``, ``wti`` columns (research specs skip absent inputs)."""
    p = pd.DataFrame(index=panel.index)
    p["sa_idx"] = panel["sa_idx"]
    p["y"] = np.log(panel["sa_idx"]).diff()  # target: Delta-log SA index, month M

    # Own persistence (published through M-1).
    p["dap_1"] = p["y"].shift(1)
    p["dap_2"] = p["y"].shift(2)
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())

    # Producer-side fares, lags >= 1 (the M-1 PPI print is published mid-M).
    if "ppi_ind" in panel:
        dppii = np.log(panel["ppi_ind"]).diff()
        p["dppii_1"] = dppii.shift(1)
        p["dppii_2"] = dppii.shift(2)
    if "ppi_com" in panel:
        dppic = np.log(panel["ppi_com"]).diff()
        p["dppic_1"] = dppic.shift(1)
        p["dppic_2"] = dppic.shift(2)

    # Fuel costs (spot, month M complete at the origin; pass-through is slow,
    # so lags/trailing means are the candidates).
    if "jet" in panel:
        djet = np.log(panel["jet"]).diff()
        p["djet_0"] = djet
        p["djet_1"] = djet.shift(1)
        p["djet_2"] = djet.shift(2)
        p["djet_3"] = djet.shift(3)
        p["jet_trail6"] = djet.shift(1).rolling(6).mean()
    if "wti" in panel:
        dwti = np.log(panel["wti"]).diff()
        p["dwti_0"] = dwti
        p["dwti_1"] = dwti.shift(1)
        p["dwti_2"] = dwti.shift(2)
        p["wti_trail6"] = dwti.shift(1).rolling(6).mean()
    return p


@dataclass(frozen=True)
class AirfaresForecast:
    target_month: pd.Timestamp
    level: float  # SA index points
    mm_pct: float  # m/m percent change (SA)
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> AirfaresForecast | None:
    """Fit the spec on all published history and forecast the next CPI month.

    The target month is the first month after the last published SA index.
    Returns None when the joint history is too short or any regressor for the
    target month is unavailable (e.g. the M-1 PPI print is missing).
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
    return AirfaresForecast(
        target_month=target,
        level=last_level * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
