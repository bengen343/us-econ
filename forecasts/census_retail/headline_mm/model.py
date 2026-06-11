"""Shared model code for the retail-sales headline forecast.

The target is Delta-log of total retail & food services sales (SA, nominal)
for report month M, forecast just before the MARTS release (~the 15th-17th
of M+1). Retail sales are nominal: the candidates are the observable
month-M drivers of its volatile components -- unit vehicle sales (~20%
weight), gasoline prices (~8%), the CPI (the broad deflator) -- plus own
history and sentiment. The bake-off (harness.py) arbitrates; SPEC_COLS
holds the winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner among PIT-LEGAL specs (2010-2026 COVID-masked: m/m RMSE
# 0.72 vs 0.87 zero, -17%; 0.78 vs 0.95 on 2017+; MAE 0.54, ~74% direction):
# the month-M gas-price change + two own lags. BEA's official month-M vehicle
# SAAR publishes ~the 25th of M+1 -- AFTER the MARTS release -- so the
# dveh_0 specs that topped the raw leaderboard (RMSE 0.65-0.66) are not
# usable in production; vehicles at lag 1 added nothing. CPI month M hurt;
# sentiment and LightGBM lost (see harness/README leaderboard).
SPEC_COLS: list[str] = ["dgas_0", "drs_1", "drs_2"]
MIN_TRAIN = 96  # months; the joint panel starts 2000 (EIA gas in BigQuery)


def build_features(inputs: dict, extra_months: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    retail: pd.Series = inputs["retail"]
    index = retail.index
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)
    p["retail"] = retail
    p["y"] = np.log(retail).diff()

    # Own history, published through M-1.
    p["drs_1"] = p["y"].shift(1)
    p["drs_2"] = p["y"].shift(2)

    # Month-M drivers, all published before the origin.
    veh = np.log(inputs["vehicles"]).diff().reindex(p.index)
    p["dveh_0"] = veh
    p["dveh_1"] = veh.shift(1)
    gas = np.log(inputs["gas"]).diff().reindex(p.index)
    p["dgas_0"] = gas
    p["dgas_1"] = gas.shift(1)
    if "cpi" in inputs:
        cpi = np.log(inputs["cpi"]).diff().reindex(p.index)
        p["dcpi_0"] = cpi
        p["dcpi_1"] = cpi.shift(1)
    if "sentiment" in inputs:
        sent = inputs["sentiment"].reindex(p.index)
        p["dsent_0"] = sent.diff()
    return p


@dataclass(frozen=True)
class RetailForecast:
    target_month: pd.Timestamp
    level: float  # SA, $M
    mm_pct: float
    n_train: int


def forecast_next(inputs: dict, cols: list[str] | None = None) -> RetailForecast | None:
    """Fit the spec on all published history and forecast the next MARTS
    month. Returns None when any target-month regressor is unavailable
    (e.g. the month-M CPI or vehicle print has not landed yet)."""
    cols = cols or SPEC_COLS
    retail: pd.Series = inputs["retail"]
    last = retail.last_valid_index()
    if last is None:
        return None
    target = last + pd.offsets.MonthBegin(1)

    p = build_features(inputs, extra_months=[target])
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
    anchor = float(retail.loc[last])
    return RetailForecast(
        target_month=target,
        level=anchor * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
