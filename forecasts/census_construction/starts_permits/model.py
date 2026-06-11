"""Shared model code for the housing starts + permits forecasts.

Both targets are Delta-log of the headline SAAR for the next report month M
(starts and permits publish together, so each is forecast from data through
M-1 plus the month-M survey/market/weather inputs that publish earlier).

The structural candidates the bake-off (harness.py) arbitrates:

  * Permits->starts bridge: the lagged log(permits/starts) gap (an ECM --
    starts converge to the permitted pipeline) and lagged permit changes.
  * Single-family bottom-up: SF starts track SF permits tightly (~90% start
    within two months of the permit); 5+ multifamily is lumpy. Forecast the
    components and recompose the total.
  * NAHB HMI month M (released ~16th of M -- before the origin).
  * 30-yr mortgage rate (weekly; month M complete at the origin).
  * NOAA national temperature deviation from the calendar-month norm
    (expanding, PIT-safe) -- the canonical winter noise in starts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Bake-off winners (2010-2026 COVID-masked; see harness/README leaderboards).
# Starts `ecm_hmi_wx_v1`: m/m RMSE 6.27 vs 8.24 carry-forward (-24%), 5.42 vs
# 6.89 on 2017+, ~76% direction -- the permits/starts ECM gap is the
# backbone, month-M HMI and the temperature deviations each add. Mortgage
# rates and the SF/MF bottom-up tied-or-lost while costing extra collectors.
# Permits `sf_mf_split_v1`: 4.63 vs 5.03 zero (best both windows) -- the
# SF/MF split of own lags beats the aggregate AR and every exogenous spec,
# and needs no inputs beyond the Census data itself.
SPEC_STARTS: list[str] = ["gap_1", "dst_1", "dhmi_0", "tdev_0", "tdev_1"]
SPEC_PERMITS: list[str] = ["dsfpm_1", "dmfpm_1"]
MIN_TRAIN = 120  # months; joint history starts 1985 (HMI)


def _month_mean(obs: pd.Series) -> pd.Series:
    frame = obs.dropna().to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    return frame.groupby("month")["value"].mean()


def build_features(inputs: dict, extra_months: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    starts: pd.DataFrame = inputs["starts"]
    permits: pd.DataFrame = inputs["permits"]
    index = starts.index.union(permits.index)
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)

    p["st_total"] = starts["total"]
    p["pm_total"] = permits["total"]
    p["st_sf"], p["st_mf24"], p["st_mf5"] = starts["sf"], starts["mf24"], starts["mf5"]
    p["pm_sf"], p["pm_mf5"] = permits["sf"], permits["mf5"]

    # Targets (and the component targets for the bottom-up specs).
    p["y_st"] = np.log(p["st_total"]).diff()
    p["y_pm"] = np.log(p["pm_total"]).diff()
    p["y_sfst"] = np.log(p["st_sf"]).diff()
    p["y_mfst"] = np.log(p["st_mf5"]).diff()

    # Own/cross history, published through M-1.
    p["dst_1"] = p["y_st"].shift(1)
    p["dst_2"] = p["y_st"].shift(2)
    p["dpm_1"] = p["y_pm"].shift(1)
    p["dpm_2"] = p["y_pm"].shift(2)
    p["gap_1"] = (np.log(p["pm_total"]) - np.log(p["st_total"])).shift(1)

    # Single-family / multifamily structure.
    p["dsfst_1"] = p["y_sfst"].shift(1)
    p["dsfpm_1"] = np.log(p["pm_sf"]).diff().shift(1)
    p["dsfpm_2"] = np.log(p["pm_sf"]).diff().shift(2)
    p["sfgap_1"] = (np.log(p["pm_sf"]) - np.log(p["st_sf"])).shift(1)
    p["dmfst_1"] = p["y_mfst"].shift(1)
    p["dmfpm_1"] = np.log(p["pm_mf5"]).diff().shift(1)
    p["mfgap_1"] = (np.log(p["pm_mf5"]) - np.log(p["st_mf5"])).shift(1)

    # NAHB HMI: month M released ~the 16th of M -- lag 0 legal at the origin.
    if "hmi" in inputs:
        hmi = inputs["hmi"].reindex(p.index)
        p["dhmi_0"] = hmi.diff()
        p["dhmi_1"] = hmi.diff().shift(1)

    # 30-yr mortgage rate (weekly -> monthly means), month M complete.
    if "mortgage" in inputs:
        mort = _month_mean(inputs["mortgage"]).reindex(p.index)
        p["dmort_0"] = mort.diff()
        p["dmort_1"] = mort.diff().shift(1)
        p["dmort_t3"] = mort.diff(3)  # 3-month change through M

    # Temperature deviation from the expanding calendar-month norm (PIT-safe).
    if "tavg" in inputs:
        tavg = inputs["tavg"].reindex(p.index)
        norm = tavg.groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
        p["tdev_0"] = tavg - norm
        p["tdev_1"] = (tavg - norm).shift(1)

    return p


@dataclass(frozen=True)
class ConstructionForecast:
    target: str  # "starts" | "permits"
    target_month: pd.Timestamp
    level: float  # SAAR, thousands of units
    mm_pct: float
    n_train: int


def _fit_predict(p: pd.DataFrame, y_col: str, cols: list[str], i: int) -> tuple[float, int] | None:
    y = p[y_col].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    if not np.isfinite(X[i]).all():
        return None
    train = np.isfinite(X).all(axis=1) & np.isfinite(y) & (np.arange(len(p)) < i)
    if train.sum() < MIN_TRAIN:
        return None
    beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
    return float(X[i] @ beta), int(train.sum())


def _forecast(
    inputs: dict, target: str, y_col: str, level_col: str, cols: list[str]
) -> ConstructionForecast | None:
    p = build_features(inputs)
    last = p[level_col].last_valid_index()
    if last is None:
        return None
    month = last + pd.offsets.MonthBegin(1)
    p = build_features(inputs, extra_months=[month])
    fit = _fit_predict(p, y_col, cols, p.index.get_loc(month))
    if fit is None:
        return None
    yhat, n_train = fit
    anchor = float(p.at[last, level_col])
    return ConstructionForecast(
        target=target,
        target_month=month,
        level=anchor * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=n_train,
    )


def forecast_starts(inputs: dict, cols: list[str] | None = None) -> ConstructionForecast | None:
    return _forecast(inputs, "starts", "y_st", "st_total", cols or SPEC_STARTS)


def forecast_permits(inputs: dict, cols: list[str] | None = None) -> ConstructionForecast | None:
    return _forecast(inputs, "permits", "y_pm", "pm_total", cols or SPEC_PERMITS)
