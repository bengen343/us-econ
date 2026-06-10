"""Shared model code for the Michigan sentiment forecasts (harness +
production).

Two forecastable objects, in index points (the ICS is published to 1
decimal):

  * ``y_p`` = prelim_M - final_{M-1}: the news between the M-1 survey and the
    prelim-M interview window (late M-1 .. early M) -- gasoline, stocks, news
    sentiment, plus own-survey momentum.
  * ``y_r`` = final_M - prelim_M: the revision. The final's sample contains
    the prelim interviews, so the revision is driven by mid-month news
    arriving after the prelim window.

The bake-off (harness.py) arbitrates; SPEC_PRELIM / SPEC_FINAL hold the
winners.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from forecasts.michigan_sentiment.headline.data import (
    month_mean,
    month_window_mean,
    survey_window_mean,
)

# Bake-off winners (2010-2026 COVID-masked; see harness/README leaderboards).
# Prelim: survey-window (25th of M-1 .. 7th of M) gas-spot + S&P changes --
# RMSE 3.61 vs 3.89 carry-forward (2010+), 3.58 vs 3.92 (2017+), ~60% dir.
# Revision: the same pair over the final's extra interview window (days 8-21
# / 8-18 of M vs the prelim window) -- RMSE 1.35 vs 1.43 zero-revision
# (2010+), best direction (~64%). DNSI/CB/retail/own-history variants and
# LightGBM all scored worse or added nothing (DNSI helped pre-2017 only).
SPEC_PRELIM: list[str] = ["dgas_sw", "dsp_sw"]
SPEC_FINAL: list[str] = ["dgas_sw_post", "dsp_sw_post"]
MIN_TRAIN = 96  # months of joint history before forecasting (prelim from 1997)


def build_features(inputs: dict, extra_months: list[pd.Timestamp] | None = None) -> pd.DataFrame:
    """Monthly feature panel from the raw inputs dict (see data.pull_panel).

    ``extra_months`` extends the index past the published Michigan history so
    a live target month gets its (already observable) regressors computed.
    """
    michigan: pd.DataFrame = inputs["michigan"]
    index = michigan.index
    if extra_months:
        index = index.union(extra_months)
    p = pd.DataFrame(index=index)
    p["prelim"] = michigan["prelim"]
    p["final"] = michigan["final"]

    # Targets.
    p["y_p"] = p["prelim"] - p["final"].shift(1)
    p["y_r"] = p["final"] - p["prelim"]

    # Own-survey structure (published through M-1 at the prelim origin; the
    # prelim for M itself is published at the final origin).
    p["dfin_1"] = p["final"].shift(1) - p["final"].shift(2)
    p["rev_1"] = p["y_r"].shift(1)
    p["seas_p"] = p["y_p"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    p["seas_r"] = p["y_r"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())

    # Gasoline (daily Gulf Coast spot + weekly retail): prelim window = early
    # month vs the prior month; revision window = what changed after the
    # prelim interviews.
    spot_early = month_window_mean(inputs["gas_spot"], 1, 7).reindex(p.index)
    spot_mid = month_window_mean(inputs["gas_spot"], 8, 21).reindex(p.index)
    spot_month = month_mean(inputs["gas_spot"]).reindex(p.index)
    p["dgas_early"] = (spot_early / spot_month.shift(1) - 1) * 100
    p["dgas_m1"] = spot_month.pct_change().shift(1) * 100
    p["dgas_late"] = (spot_mid / spot_early - 1) * 100

    retail_early = month_window_mean(inputs["gas_retail"], 1, 8).reindex(p.index)
    retail_mid = month_window_mean(inputs["gas_retail"], 9, 22).reindex(p.index)
    retail_month = month_mean(inputs["gas_retail"]).reindex(p.index)
    p["dretail_early"] = (retail_early / retail_month.shift(1) - 1) * 100
    p["dretail_late"] = (retail_mid / retail_early - 1) * 100

    # Stocks.
    sp_early = month_window_mean(inputs["sp500"], 1, 7).reindex(p.index)
    sp_mid = month_window_mean(inputs["sp500"], 8, 18).reindex(p.index)
    sp_month = month_mean(inputs["sp500"]).reindex(p.index)
    p["dsp_early"] = (sp_early / sp_month.shift(1) - 1) * 100
    p["dsp_m1"] = sp_month.pct_change().shift(1) * 100
    p["dsp_late"] = (sp_mid / sp_early - 1) * 100

    # SF Fed Daily News Sentiment (standardized-ish scale; use differences
    # x100). The early window stops at day 5 -- the index updates weekly with
    # a few days' lag, so day 5 is what's reliably published by the prelim
    # origin (~day 10).
    dnsi_early = month_window_mean(inputs["dnsi"], 1, 5).reindex(p.index)
    dnsi_mid = month_window_mean(inputs["dnsi"], 8, 18).reindex(p.index)
    dnsi_window_early = month_window_mean(inputs["dnsi"], 1, 7).reindex(p.index)
    dnsi_month = month_mean(inputs["dnsi"]).reindex(p.index)
    p["ddnsi_early"] = (dnsi_early - dnsi_month.shift(1)) * 100
    p["ddnsi_m1"] = dnsi_month.diff().shift(1) * 100
    p["ddnsi_late"] = (dnsi_mid - dnsi_window_early) * 100

    # Conference Board confidence, lag 1 (the month-M CB release can land
    # after the Michigan final -- only M-1 is safe at either origin).
    cb = inputs["cb"].reindex(p.index)
    p["dcb_1"] = cb.diff().shift(1)

    # Survey-window-aligned variants: the prelim interviews run ~day 25 of
    # M-1 through ~day 7 of M, so the sharpest "what changed between surveys"
    # regressor is the survey-window-to-survey-window change.
    for name, series, kind in (
        ("gas_sw", inputs["gas_spot"], "pct"),
        ("retail_sw", inputs["gas_retail"], "pct"),
        ("sp_sw", inputs["sp500"], "pct"),
        ("dnsi_sw", inputs["dnsi"], "diff"),
    ):
        end_day = 5 if name == "dnsi_sw" else 7  # DNSI publishes with a lag
        window = survey_window_mean(series, 25, end_day).reindex(p.index)
        if kind == "pct":
            p[f"d{name}"] = window.pct_change() * 100
        else:
            p[f"d{name}"] = window.diff() * 100
        # Revision counterpart: the final's extra interviews (~days 8-21 of
        # M) vs the prelim window.
        late_days = (8, 18) if name in ("sp_sw", "dnsi_sw") else (8, 21)
        late = month_window_mean(series, *late_days).reindex(p.index)
        if kind == "pct":
            p[f"d{name}_post"] = (late / window - 1) * 100
        else:
            p[f"d{name}_post"] = (late - window) * 100
    return p


@dataclass(frozen=True)
class SentimentForecast:
    target: str  # "ics_prelim" | "ics_final"
    target_month: pd.Timestamp
    level: float  # forecast ICS
    change: float  # vs the anchor (M-1 final for prelim; the prelim for final)
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


def forecast_prelim(inputs: dict, cols: list[str] | None = None) -> SentimentForecast | None:
    """Forecast the next preliminary ICS: the month after the last final."""
    michigan = inputs["michigan"]
    last_final = michigan["final"].last_valid_index()
    if last_final is None:
        return None
    target = last_final + pd.offsets.MonthBegin(1)
    if target in michigan.index and np.isfinite(michigan["prelim"].get(target, np.nan)):
        return None  # that prelim is already published

    p = build_features(inputs, extra_months=[target])
    fit = _fit_predict(p, "y_p", cols or SPEC_PRELIM, p.index.get_loc(target))
    if fit is None:
        return None
    yhat, n_train = fit
    anchor = float(michigan.at[last_final, "final"])
    return SentimentForecast(
        target="ics_prelim",
        target_month=target,
        level=anchor + yhat,
        change=yhat,
        n_train=n_train,
    )


def forecast_final(inputs: dict, cols: list[str] | None = None) -> SentimentForecast | None:
    """Forecast the final ICS for the last published preliminary's month."""
    michigan = inputs["michigan"]
    pending = michigan[michigan["prelim"].notna() & michigan["final"].isna()]
    if pending.empty:
        return None
    target = pending.index[-1]

    p = build_features(inputs)
    fit = _fit_predict(p, "y_r", cols or SPEC_FINAL, p.index.get_loc(target))
    if fit is None:
        return None
    yhat, n_train = fit
    anchor = float(michigan.at[target, "prelim"])
    return SentimentForecast(
        target="ics_final",
        target_month=target,
        level=anchor + yhat,
        change=yhat,
        n_train=n_train,
    )
