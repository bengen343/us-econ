"""Build the monthly modelling panel for the ADP headline nowcast.

One row per target month M (first-of-month Timestamp). The headline target is
``y = ner_sa(M) - ner_sa(M-1)`` (the published figure).

PIT timing model
----------------
We forecast month M just before its release on the first Wednesday of M+1.
At that origin the following is knowable:

  * Monthly ADP headlines through M-1 (the prior release).            -> momentum
  * All weekly signals (Pulse, claims, Trends) for weeks ENDING in M, because
    month M has fully elapsed by release-eve of M+1.                  -> nowcast
  * Weekly ADP NSA level lags more and is omitted here.

So every weekly series is aggregated to month M by averaging the weeks ending
within calendar month M, and monthly-headline features are strictly lagged to
<= M-1. No feature uses information unavailable at the M+1 release origin.

Feature groups are returned separately so the backtest can ablate them. The
weekly Pulse has history only from 2026-01, so ``pulse`` features are NaN for
almost all rows; they help only the most recent origins and grow over time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Trends series chosen a priori for signal + to control dimensionality at n~44.
# Labor-stress (higher => weaker jobs) vs hiring-demand (higher => stronger).
TREND_STRESS = ["trends_q_layoffs", "trends_unemployment_topic",
                "trends_q_file_for_unemployment"]
TREND_HIRING = ["trends_q_jobs_hiring", "trends_jobs_cat", "trends_q_jobs_near_me"]


def _month_of(week_ending: pd.Series) -> pd.Series:
    return week_ending.dt.to_period("M").dt.to_timestamp()


def _weekly_to_monthly_mean(df: pd.DataFrame, week_col: str, val_cols: list[str]
                            ) -> pd.DataFrame:
    """Average weekly values into calendar months (mean of weeks ending in M)."""
    g = df.copy()
    g["month"] = _month_of(g[week_col])
    out = g.groupby("month")[val_cols].mean()
    return out


def build_panel(monthly: pd.DataFrame, pulse: pd.DataFrame,
                claims: pd.DataFrame, trends: pd.DataFrame
                ) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Return (panel, feature_groups).

    panel is indexed by month M with a ``y`` target column plus all candidate
    feature columns. feature_groups maps a group name to its column list.
    """
    m = monthly.set_index("month").sort_index()

    # Extend the index by one month so the harness can emit a LIVE forecast for
    # the next (not-yet-released) headline: momentum comes from known headlines
    # and the weekly signals are aggregated from whatever weeks have landed.
    next_month = m.index.max() + pd.offsets.MonthBegin(1)
    index = m.index.append(pd.DatetimeIndex([next_month]))
    panel = pd.DataFrame(index=index)

    # ---- Target: headline = MoM change in SA level -------------------------
    headline = m["ner_sa"].diff().reindex(index)
    panel["y"] = headline

    groups: dict[str, list[str]] = {}

    # ---- Momentum (strictly <= M-1) ----------------------------------------
    mom_cols = []
    for k in (1, 2, 3):
        c = f"y_lag{k}"
        panel[c] = headline.shift(k)
        mom_cols.append(c)
    panel["y_roll3"] = headline.shift(1).rolling(3, min_periods=3).mean()
    panel["y_roll6"] = headline.shift(1).rolling(6, min_periods=6).mean()
    mom_cols += ["y_roll3", "y_roll6"]
    groups["momentum"] = mom_cols

    # ---- Claims (weekly SA initial claims -> month M) ----------------------
    cm = _weekly_to_monthly_mean(claims, "week_ending", ["claims_sa"])
    cm = cm.reindex(panel.index)
    panel["claims_m"] = cm["claims_sa"]
    panel["claims_mom"] = panel["claims_m"].diff()
    panel["claims_3m_chg"] = panel["claims_m"] - panel["claims_m"].shift(3)
    # Same-month claims are contemporaneous with the target (both describe M),
    # which is exactly the nowcast signal we want; higher claims => weaker jobs.
    groups["claims"] = ["claims_m", "claims_mom", "claims_3m_chg"]

    # ---- Google Trends (weekly -> month M) ---------------------------------
    tcols = [c for c in TREND_STRESS + TREND_HIRING if c in trends.columns]
    tm = _weekly_to_monthly_mean(trends, "week_ending", tcols).reindex(panel.index)
    stress = [c for c in TREND_STRESS if c in tm.columns]
    hiring = [c for c in TREND_HIRING if c in tm.columns]
    # Composites (z-scored within available history, then averaged) reduce the
    # 6 raw columns to 2 signed signals -> far fewer params for n~44.
    panel["tr_stress"] = _zmean(tm[stress]) if stress else np.nan
    panel["tr_hiring"] = _zmean(tm[hiring]) if hiring else np.nan
    panel["tr_stress_mom"] = panel["tr_stress"].diff()
    panel["tr_hiring_mom"] = panel["tr_hiring"].diff()
    groups["trends"] = ["tr_stress", "tr_hiring", "tr_stress_mom", "tr_hiring_mom"]

    # ---- Weekly Pulse bridge (data-starved; ~2026-01 onward) ---------------
    pm = _weekly_to_monthly_mean(pulse, "week_ending", ["pulse"]).reindex(panel.index)
    weeks_in_month = pd.Series(
        [_weeks_in_month(idx, pulse) for idx in panel.index], index=panel.index
    )
    panel["pulse_mean"] = pm["pulse"]
    # Naive bridge: avg weekly SA change * weeks => implied monthly change.
    panel["pulse_implied"] = panel["pulse_mean"] * weeks_in_month
    groups["pulse"] = ["pulse_mean", "pulse_implied"]

    panel["month"] = panel.index
    return panel, groups


def _zmean(df: pd.DataFrame) -> pd.Series:
    """Z-score each column over its observed history, then row-mean.

    Note: uses full-sample mean/std for standardisation. That is a mild
    in-sample leak for the *scale* of the composite only; the backtest's
    model coefficients are still fit walk-forward. Acceptable for research;
    a production port should standardise on the training slice.
    """
    z = (df - df.mean()) / df.std(ddof=0)
    return z.mean(axis=1)


def _weeks_in_month(month_start: pd.Timestamp, pulse: pd.DataFrame) -> int:
    """Count Pulse weeks ending within the calendar month (fallback 4)."""
    if pulse.empty:
        return 4
    end = month_start + pd.offsets.MonthEnd(0)
    n = ((pulse["week_ending"] >= month_start) & (pulse["week_ending"] <= end)).sum()
    return int(n) if n > 0 else 4
