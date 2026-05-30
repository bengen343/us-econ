"""Monthly modelling panel for the unemployment-rate nowcast.

One row per target month M (first-of-month Timestamp). The release publishes the
rate to one decimal (e.g. 4.2), so the user's bar is to "call it exactly" — i.e.
predict the same rounded 0.1 value. We carry BOTH framings the user asked for:

    y_level = LNS14000000(M)               # the rate, percent
    y_chg   = LNS14000000(M) - (M-1)       # MoM change in the rate (pp)

PIT timing model
----------------
Forecast month M just before its release on the **first Friday of M+1**. The UR
is extremely persistent (it moves 0.0 / +/-0.1 most months), so RW (last
month's rate) is a very strong baseline; the job is to call the small moves.

Knowable at the origin:
  * UR through M-1 (momentum / persistence).
  * Weekly claims for weeks ENDING in M — especially the insured unemployment
    rate (``iur_sa``), a direct weekly coincident analogue of the UR, plus
    continued and initial claims.                                   -> nowcast
  * Google Trends (unemployment search) for weeks ending in M.      -> nowcast
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UR = "LNS14000000"  # civilian unemployment rate, SA (percent)
BLS_SERIES = [UR]

TREND_STRESS = [
    "trends_q_file_for_unemployment",
    "trends_unemployment_topic",
    "trends_q_unemployment_benefits",
    "trends_q_unemployment_office",
]


def _month_of(week_ending: pd.Series) -> pd.Series:
    return week_ending.dt.to_period("M").dt.to_timestamp()


def _weekly_to_monthly_mean(df: pd.DataFrame, val_cols: list[str]) -> pd.DataFrame:
    g = df.copy()
    g["month"] = _month_of(g["week_ending"])
    return g.groupby("month")[val_cols].mean()


def _zmean(df: pd.DataFrame) -> pd.Series:
    if df.shape[1] == 0:
        return pd.Series(np.nan, index=df.index)
    z = (df - df.mean()) / df.std(ddof=0)
    return z.mean(axis=1)


def build_panel(
    bls: pd.DataFrame, claims: pd.DataFrame, trends: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Return (panel, feature_groups). panel is indexed by month M."""
    b = bls.set_index("month").sort_index()
    next_month = b.index.max() + pd.offsets.MonthBegin(1)
    index = b.index.append(pd.DatetimeIndex([next_month]))
    panel = pd.DataFrame(index=index)

    ur = b[UR].reindex(index)
    panel["y_level"] = ur
    panel["y_chg"] = ur.diff()
    panel["ur_lag1"] = ur.shift(1)  # = the RW level forecast
    # COVID spike (UR hit 14.8% Apr-2020) — unforecastable outlier.
    panel["is_covid"] = (index >= "2020-03-01") & (index <= "2021-06-01")

    groups: dict[str, list[str]] = {}

    # ---- Momentum / persistence (strictly <= M-1) --------------------------
    chg = panel["y_chg"]
    panel["ur_l1"] = ur.shift(1)
    panel["ur_l2"] = ur.shift(2)
    panel["ur_l3"] = ur.shift(3)
    panel["ur_chg_l1"] = chg.shift(1)
    panel["ur_chg_l2"] = chg.shift(2)
    panel["ur_chg_roll3"] = chg.shift(1).rolling(3, min_periods=3).mean()
    groups["momentum"] = ["ur_l1", "ur_l2", "ur_l3", "ur_chg_l1", "ur_chg_l2", "ur_chg_roll3"]

    # ---- Insured unemployment rate (weeks in M; direct coincident proxy) ----
    cm = _weekly_to_monthly_mean(
        claims, ["iur_sa", "claims_continued_sa", "claims_initial_sa"]
    ).reindex(index)
    panel["iur_m"] = cm["iur_sa"]
    panel["iur_mom"] = panel["iur_m"].diff()
    panel["iur_3m"] = panel["iur_m"] - panel["iur_m"].shift(3)
    groups["iur"] = ["iur_m", "iur_mom", "iur_3m"]

    # ---- Other claims (weeks in M) -----------------------------------------
    panel["cont_m"] = cm["claims_continued_sa"]
    panel["cont_mom"] = panel["cont_m"].diff()
    panel["init_m"] = cm["claims_initial_sa"]
    panel["init_3m"] = panel["init_m"] - panel["init_m"].shift(3)
    groups["claims"] = ["cont_m", "cont_mom", "init_m", "init_3m"]

    # ---- Google Trends (unemployment search; weeks in M) -------------------
    tcols = [c for c in TREND_STRESS if c in trends.columns]
    tm = _weekly_to_monthly_mean(trends, tcols).reindex(index)
    panel["tr_unemp"] = _zmean(tm[tcols])
    panel["tr_unemp_mom"] = panel["tr_unemp"].diff()
    groups["trends"] = ["tr_unemp", "tr_unemp_mom"]

    panel["month"] = panel.index
    return panel, groups
