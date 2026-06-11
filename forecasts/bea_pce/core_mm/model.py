"""Shared model code for the core-PCE m/m forecast.

The target is Delta-log of the core PCE price index (DPCCRG) for report
month M. Month-M core CPI is the translation backbone; the PPI add-ons
(portfolio management, healthcare, airfares) and the S&P 500 cover the
components PCE sources outside the CPI. All regressors are published weeks
before the origin. The bake-off (harness.py) arbitrates; SPEC_COLS holds
the winner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winner (2012-2026 COVID-masked: m/m MAE 0.052pp / RMSE 0.072 vs
# 0.101 AR(1) and 0.233 carry-forward; 0.085 vs 0.116/0.278 on 2017+; best
# above/below-median direction 80-84%): month-M core CPI -- the translation
# backbone -- plus month-M PPI airfares, the famous CPI/PCE wedge component.
# Denser specs (S&P portfolio proxy, physicians+hospitals PPI, seasonal,
# kitchen) bought <=0.003 RMSE for 2-6 more regressors and extra collector
# series; LightGBM lost. The actual PPI portfolio-management series only
# starts 2022 (NAICS recode) -- the documented upgrade once history accrues.
SPEC_COLS: list[str] = ["dccpi_0", "dair_0"]
MIN_TRAIN = 96


def build_features(
    panel: pd.DataFrame, extra_months: list[pd.Timestamp] | None = None
) -> pd.DataFrame:
    index = panel.index
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)
    p["core_pce"] = panel["core_pce"].reindex(index)
    p["y"] = np.log(p["core_pce"]).diff()

    p["dpce_1"] = p["y"].shift(1)
    p["dpce_2"] = p["y"].shift(2)

    # Month-M source data (all published ~2 weeks before the origin).
    dccpi = np.log(panel["core_cpi"]).diff().reindex(index)
    p["dccpi_0"] = dccpi
    p["dccpi_1"] = dccpi.shift(1)

    for col, short in (
        ("ppi_portfolio", "dpm"),
        ("ppi_physicians", "dphy"),
        ("ppi_hospitals", "dhos"),
        ("ppi_airfares", "dair"),
    ):
        if col in panel:
            dlog = np.log(panel[col]).diff().reindex(index)
            p[f"{short}_0"] = dlog
            p[f"{short}_1"] = dlog.shift(1)

    if "sp500" in panel:
        dsp = np.log(panel["sp500"]).diff().reindex(index)
        p["dsp_0"] = dsp
        p["dsp_1"] = dsp.shift(1)

    # NSA PPI details carry seasonality the SA target does not -- give fitted
    # specs an expanding calendar-month control.
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    return p


@dataclass(frozen=True)
class PceForecast:
    target_month: pd.Timestamp
    level: float  # index (2017=100)
    mm_pct: float
    n_train: int


def forecast_next(panel: pd.DataFrame, cols: list[str] | None = None) -> PceForecast | None:
    """Fit the spec on all published history and forecast the next PCE month.

    Returns None when any target-month regressor is unavailable (e.g. the
    month-M CPI/PPI prints have not landed yet -- right after a PCE release
    the target rolls forward and the job idles until mid-month)."""
    cols = cols or SPEC_COLS
    last = panel["core_pce"].last_valid_index()
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
    anchor = float(panel.at[last, "core_pce"])
    return PceForecast(
        target_month=target,
        level=anchor * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=int(train.sum()),
    )
