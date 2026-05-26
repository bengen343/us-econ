"""Feature engineering for the direction forecast. Production port of the
relevant pieces of forecasts/claims/initial_claims/lgbm_test/harness.py.

Builds a panel of (origin -> feature row) with one row per Saturday-ending
SA week. Features are:
  * target lags (sa_input at origin - k weeks, for k in TARGET_LAGS)
  * sa-week-over-week diffs at lags 1, 2, 4
  * sa rolling means over the last 4 and 8 weeks
  * seasonal lookups: sa at origin - 52 weeks (sa_seas52) and sa at the
    target week's calendar slot one year earlier (sa_seas_target)
  * month, isoweek calendar features
  * each Google Trends signal at the configured trends-lag (default 4)
  * ADP NSA NER (week-over-week diff) at the configured adp-diff-lag,
    with graceful fallback if the freshest ADP week is older than expected

The label for direction training is:
    y_dir = 1 if sa_actual[T+1] > sa_input[T] else 0
We build it in the same panel for convenience; the inference step looks at
the row whose origin == latest_sa_week and drops y_dir there (since it isn't
yet observed).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from forecasts.claims.initial_claims.direction_lgbm.series import (
    ADP_DIFF_LAG_FALLBACKS,
    ADP_DIFF_LAG_PRIMARY,
    ADP_NER_COL,
    TARGET_LAGS,
    TRENDS_LAG,
)


@dataclass(frozen=True)
class PanelInputs:
    """Three weekly DataFrames keyed on Saturday week_ending."""
    sa: pd.DataFrame      # columns: week_ending, sa_input, sa_actual
    trends: pd.DataFrame  # columns: week_ending, then one column per trends series
    adp: pd.DataFrame     # columns: week_ending, ner  (NSA total US employment, weekly)


def resolve_adp_diff_lag(inputs: PanelInputs, latest_origin: pd.Timestamp) -> int:
    """Pick the freshest ADP lag whose value is observable at `latest_origin`.

    Returns the smallest lag in [PRIMARY] + FALLBACKS where ADP has a value
    at week_ending = latest_origin - lag*7d.
    """
    adp = inputs.adp.set_index("week_ending")[ADP_NER_COL]
    for lag in [ADP_DIFF_LAG_PRIMARY, *ADP_DIFF_LAG_FALLBACKS]:
        ref = latest_origin - pd.Timedelta(days=7 * lag)
        prev = latest_origin - pd.Timedelta(days=7 * (lag + 1))
        if ref in adp.index and prev in adp.index and pd.notna(adp.loc[ref]) and pd.notna(adp.loc[prev]):
            return lag
    raise RuntimeError(
        f"No ADP NSA NER value available at any of lag {[ADP_DIFF_LAG_PRIMARY, *ADP_DIFF_LAG_FALLBACKS]} "
        f"weeks before origin {latest_origin.date()}; latest ADP week is "
        f"{adp.dropna().index.max().date()}"
    )


def build_panel(inputs: PanelInputs, adp_diff_lag: int) -> tuple[pd.DataFrame, list[str]]:
    """Build the modelling panel. Returns (panel_df, feature_columns).

    panel_df has one row per origin (Saturday week_ending) with all feature
    columns. The `y_dir` column is the training label where observable, NaN
    where not.
    """
    sa = inputs.sa.set_index("week_ending").sort_index()
    sa_input = sa["sa_input"]
    sa_actual = sa["sa_actual"]

    panel = pd.DataFrame(index=sa.index)

    # SA-based features (use sa_input — what was knowable at origin T)
    for k in TARGET_LAGS:
        panel[f"sa_lag{k}"] = sa_input.shift(k)
    for k in (1, 2, 4):
        panel[f"sa_diff{k}"] = sa_input - sa_input.shift(k)
    for w in (4, 8):
        panel[f"sa_rollmean{w}"] = sa_input.rolling(window=w, min_periods=w).mean()

    # Seasonal: SA at origin - 52 weeks, and SA at target week - 52 weeks
    panel["sa_seas52"] = sa_input.shift(52)
    panel["sa_seas_target"] = sa_input.shift(51)

    # Calendar
    panel["month"] = panel.index.month
    panel["isoweek"] = panel.index.isocalendar().week.astype(int)

    # Trends signals at the configured lag.
    trends = inputs.trends.set_index("week_ending").sort_index()
    trends = trends.reindex(panel.index).ffill(limit=2)
    for col in trends.columns:
        panel[f"{col}_lag{TRENDS_LAG}"] = trends[col].shift(TRENDS_LAG)

    # ADP NSA week-over-week diff at the resolved lag.
    adp = inputs.adp.set_index("week_ending").sort_index()[ADP_NER_COL].astype(float)
    adp = adp.reindex(panel.index).ffill(limit=1)
    panel[f"adp_ner_diff_lag{adp_diff_lag}"] = (
        adp.shift(adp_diff_lag) - adp.shift(adp_diff_lag + 1)
    )

    # Label: direction at the row indexed by origin T.
    panel["y_actual"] = sa_actual.shift(-1)
    panel["this_week"] = sa_input
    panel["y_dir"] = (panel["y_actual"] > panel["this_week"]).astype("Int64")
    # Where target not observed, mark y_dir as missing.
    panel.loc[panel["y_actual"].isna(), "y_dir"] = pd.NA
    panel["origin"] = panel.index

    feature_cols = [
        c for c in panel.columns
        if c not in ("y_actual", "this_week", "y_dir", "origin")
    ]
    return panel, feature_cols


def trim_features(panel: pd.DataFrame, feature_cols: Iterable[str]) -> pd.DataFrame:
    """Drop training rows missing any feature OR the label."""
    needed = list(feature_cols) + ["y_dir"]
    valid = panel.dropna(subset=needed).copy()
    return valid
