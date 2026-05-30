"""Monthly modelling panel for the NFP headline nowcast.

One row per target month M (first-of-month Timestamp). Target:

    y = CES0000000001(M) - CES0000000001(M-1)      # MoM change in SA total
                                                    # nonfarm payrolls, thousands

PIT timing model
----------------
We forecast month M just before its release on the **first Friday of M+1**. At
that origin the following is knowable (and nothing else is used):

  * NFP MoM changes through M-1 (the prior release).                 -> momentum
  * ADP monthly headline for M: released the first Wednesday of M+1,
    ~2 days before NFP.                                              -> nowcast
  * Weekly signals (claims, ADP Pulse, Trends) for weeks ENDING in M, because
    month M has fully elapsed by the M+1 release.                    -> nowcast
  * Challenger job-cut/hiring totals for M: released first Thursday of M+1.
  * BLS sub-components (e.g. temp-help employment) only through M-1, because
    component detail ships *with* the M release -> strictly lagged.  -> leading

Feature groups are returned separately so the harness can ablate them. Groups
with short history (adp from 2010, trends from 2021, pulse from 2026, challenger
layoffs ~2025) are simply NaN on older rows; the walk-forward harness evaluates
each spec on the origins where its features exist.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# BLS series we consume. Target first, then a leading sub-component.
NFP_TOTAL = "CES0000000001"  # total nonfarm, SA (the target level)
TEMP_HELP = "CES6056132001"  # temp-help services, SA (leading indicator)
BLS_SERIES = [NFP_TOTAL, TEMP_HELP]

# Trends chosen a priori (labor-stress up => weaker jobs; hiring up => stronger).
TREND_STRESS = ["trends_q_layoffs", "trends_unemployment_topic", "trends_q_file_for_unemployment"]
TREND_HIRING = ["trends_q_jobs_hiring", "trends_jobs_cat", "trends_q_jobs_near_me"]


def _month_of(week_ending: pd.Series) -> pd.Series:
    return week_ending.dt.to_period("M").dt.to_timestamp()


def _weekly_to_monthly_mean(df: pd.DataFrame, val_cols: list[str]) -> pd.DataFrame:
    g = df.copy()
    g["month"] = _month_of(g["week_ending"])
    return g.groupby("month")[val_cols].mean()


def _zmean(df: pd.DataFrame) -> pd.Series:
    """Z-score each column over its observed history, then row-mean.

    Uses full-sample mean/std for the composite *scale* only (a mild in-sample
    leak for scale; model coefficients are still fit walk-forward). A production
    port should standardise on the training slice.
    """
    if df.shape[1] == 0:
        return pd.Series(np.nan, index=df.index)
    z = (df - df.mean()) / df.std(ddof=0)
    return z.mean(axis=1)


def build_panel(
    bls: pd.DataFrame,
    claims: pd.DataFrame,
    adp: pd.DataFrame,
    pulse: pd.DataFrame,
    trends: pd.DataFrame,
    challenger: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Return (panel, feature_groups). panel is indexed by month M."""
    b = bls.set_index("month").sort_index()

    # Extend index one month so the harness can emit a LIVE forecast for the
    # next (not-yet-released) headline from whatever signals have landed.
    next_month = b.index.max() + pd.offsets.MonthBegin(1)
    index = b.index.append(pd.DatetimeIndex([next_month]))
    panel = pd.DataFrame(index=index)

    # ---- Target: MoM change in SA total nonfarm (thousands) ----------------
    nfp = b[NFP_TOTAL].reindex(index)
    y = nfp.diff()
    panel["y"] = y
    # COVID shock months are unforecastable outliers; flag for masking.
    panel["is_covid"] = (index >= "2020-03-01") & (index <= "2021-06-01")

    groups: dict[str, list[str]] = {}

    # ---- Momentum (strictly <= M-1) ----------------------------------------
    mom = []
    for k in (1, 2, 3):
        c = f"y_lag{k}"
        panel[c] = y.shift(k)
        mom.append(c)
    panel["y_roll3"] = y.shift(1).rolling(3, min_periods=3).mean()
    panel["y_roll6"] = y.shift(1).rolling(6, min_periods=6).mean()
    panel["y_roll12"] = y.shift(1).rolling(12, min_periods=12).mean()
    mom += ["y_roll3", "y_roll6", "y_roll12"]
    groups["momentum"] = mom

    # ---- Temp-help employment MoM (leading; strictly <= M-1) ---------------
    th = b[TEMP_HELP].reindex(index)
    th_chg = th.diff()
    panel["temp_help_chg_lag1"] = th_chg.shift(1)
    panel["temp_help_chg_lag2"] = th_chg.shift(2)
    groups["temphelp"] = ["temp_help_chg_lag1", "temp_help_chg_lag2"]

    # ---- Claims (weekly SA -> month M; contemporaneous nowcast) ------------
    cm = _weekly_to_monthly_mean(claims, ["claims_initial_sa", "claims_continued_sa"]).reindex(
        index
    )
    panel["claims_init_m"] = cm["claims_initial_sa"]
    panel["claims_init_mom"] = panel["claims_init_m"].diff()
    panel["claims_init_3m"] = panel["claims_init_m"] - panel["claims_init_m"].shift(3)
    panel["claims_cont_mom"] = cm["claims_continued_sa"].diff()
    groups["claims"] = ["claims_init_m", "claims_init_mom", "claims_init_3m", "claims_cont_mom"]

    # ---- ADP monthly headline for M (knowable ~2 days pre-NFP) -------------
    # ADP ner_sa is a level in PERSONS; /1000 to match CES units (thousands).
    a = adp.set_index("month").sort_index()["adp_sa"].reindex(index) / 1000.0
    panel["adp_chg"] = a.diff()  # ADP monthly headline (k)
    panel["adp_chg_lag1"] = a.diff().shift(1)
    groups["adp"] = ["adp_chg", "adp_chg_lag1"]

    # ---- ADP weekly Pulse (data-starved; ~2026-01 onward) ------------------
    pm = _weekly_to_monthly_mean(pulse, ["pulse"]).reindex(index)
    weeks = pd.Series([_weeks_in_month(idx, pulse) for idx in index], index=index)
    panel["pulse_mean"] = pm["pulse"]
    panel["pulse_implied"] = panel["pulse_mean"] * weeks
    groups["pulse"] = ["pulse_mean", "pulse_implied"]

    # ---- Google Trends (weekly -> month M) ---------------------------------
    tcols = [c for c in TREND_STRESS + TREND_HIRING if c in trends.columns]
    tm = _weekly_to_monthly_mean(trends, tcols).reindex(index)
    panel["tr_stress"] = _zmean(tm[[c for c in TREND_STRESS if c in tm.columns]])
    panel["tr_hiring"] = _zmean(tm[[c for c in TREND_HIRING if c in tm.columns]])
    panel["tr_stress_mom"] = panel["tr_stress"].diff()
    panel["tr_hiring_mom"] = panel["tr_hiring"].diff()
    groups["trends"] = ["tr_stress", "tr_hiring", "tr_stress_mom", "tr_hiring_mom"]

    # ---- Challenger (month M; layoffs recent-only, hiring from 2017) -------
    ch = challenger.set_index("month").sort_index().reindex(index)
    # Log-scale the heavy-tailed announcement counts; YoY change is the signal.
    lay = np.log1p(ch["challenger_layoffs"])
    panel["challenger_layoffs_l"] = lay
    panel["challenger_hiring_l"] = np.log1p(ch["challenger_hiring"])
    panel["challenger_layoffs_yoy"] = lay - lay.shift(12)
    groups["challenger"] = ["challenger_layoffs_l", "challenger_hiring_l", "challenger_layoffs_yoy"]

    panel["month"] = panel.index
    return panel, groups


def _weeks_in_month(month_start: pd.Timestamp, weekly: pd.DataFrame) -> int:
    if weekly.empty:
        return 4
    end = month_start + pd.offsets.MonthEnd(0)
    n = ((weekly["week_ending"] >= month_start) & (weekly["week_ending"] <= end)).sum()
    return int(n) if n > 0 else 4
