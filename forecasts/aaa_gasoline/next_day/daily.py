"""Daily-frequency next-day model fit on AAA's own history (``daily_ar_rbob_v1``).

With ~5 months of daily AAA prices (2026-10 review, 117 live-scored days) a
regression on the true daily target beat the weekly-ECM-plus-momentum blend
(v2) out of sample: RMSE 1.29c vs 1.41c (DM -2.2 vs v2, p<0.05), and the gain held
in both halves of the window. Next-day change, no intercept:

    AAA_{t+1} - AAA_t ~ d1 + d2 + ecm_step + drb1 + drb2

* d1, d2     -- the last two AAA day-over-day changes. Momentum is strong but
               decays (in-sample ~+0.77 on d1, ~-0.25 on d2).
* ecm_step   -- v2's daily ECM drift (seasonal-EC symmetric RBOB ECM fit on the
               long EIA weekly history, weekly move / 5): the pull toward the
               RBOB-implied equilibrium that momentum alone can't see.
* drb1, drb2 -- the last two settled RBOB daily changes (pass-through).

Asymmetric ("rockets and feathers") splits of d1 or the RBOB changes were tested
on the same data and did not help, matching the weekly finding.

Everything is point-in-time: the row for day t only uses AAA through t and
futures / EIA dated before t -- the morning run never sees an unsettled RBOB
bar. The panel re-derives the ECM step for every historical day (one weekly
refit each, ~0.1s), so the build cost grows slowly with history.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from forecasts.aaa_gasoline.next_day import model
from forecasts.aaa_gasoline.next_day.harness import build_weekly_panel

FEATURES = ["d1", "d2", "ecm_step", "drb1", "drb2"]
MIN_TRAIN = 30  # fewer training days than this and the model is not produced


def build_panel(
    aaa: pd.Series,
    futures: pd.DataFrame,
    eia_retail: pd.Series,
    spec: model.Spec,
    days_per_week: float = 5.0,
) -> pd.DataFrame:
    """One row per AAA day t: features known on the morning of t, plus the target
    y = AAA_{t+1} - AAA_t (NaN for the latest day and across scrape gaps).

    Also carries the ECM diagnostics (equilibrium, weekly_move, rbob) used for the
    output row. d1/d2/y need consecutive days; a gap leaves them NaN.
    """
    aaa = aaa.copy()
    aaa.index = pd.to_datetime(aaa.index)
    eia_retail = eia_retail.copy()
    eia_retail.index = pd.to_datetime(eia_retail.index)
    one = pd.Timedelta(days=1)

    rows = []
    for i, t in enumerate(aaa.index):
        cutoff = t - one
        fut = futures[futures.index <= cutoff]
        rbob = fut["rbob"].dropna()
        if len(rbob) < 3:
            continue
        panel = build_weekly_panel(eia_retail[eia_retail.index <= cutoff], fut)
        if panel.empty:
            continue
        anchor = float(aaa.iloc[i])
        nd = model.next_day_forecast(
            panel, spec, anchor, float(rbob.iloc[-1]), days_per_week, as_of_month=t.month
        )

        nxt = i + 1
        y = (
            float(aaa.iloc[nxt] - aaa.iloc[i])
            if nxt < len(aaa) and aaa.index[nxt] - t == one
            else math.nan
        )
        rows.append(
            {
                "date": t,
                "anchor": anchor,
                "d1": _change(aaa, i),
                "d2": _change(aaa, i - 1),
                "ecm_step": nd.weekly_move / days_per_week,
                "drb1": float(rbob.iloc[-1] - rbob.iloc[-2]),
                "drb2": float(rbob.iloc[-2] - rbob.iloc[-3]),
                "rbob": float(rbob.iloc[-1]),
                "equilibrium": nd.equilibrium,
                "weekly_move": nd.weekly_move,
                "n_weekly": len(panel),
                "y": y,
            }
        )
    return pd.DataFrame(rows).set_index("date")


def _change(aaa: pd.Series, j: int) -> float:
    """AAA change from position j-1 to j, if both days exist and are adjacent."""
    if j < 1 or aaa.index[j] - aaa.index[j - 1] != pd.Timedelta(days=1):
        return math.nan
    return float(aaa.iloc[j] - aaa.iloc[j - 1])


def fit(train: pd.DataFrame) -> np.ndarray:
    """OLS (no intercept) of y on FEATURES over complete rows."""
    tr = train.dropna(subset=FEATURES + ["y"])
    coef, *_ = np.linalg.lstsq(tr[FEATURES].to_numpy(), tr["y"].to_numpy(), rcond=None)
    return coef


def n_train(train: pd.DataFrame) -> int:
    return int(train.dropna(subset=FEATURES + ["y"]).shape[0])


def pit_errors(panel: pd.DataFrame) -> pd.Series:
    """Out-of-sample one-step errors (forecast - actual, $/gal): each day predicted
    from a fit on the days strictly before it, once MIN_TRAIN days exist."""
    errs = {}
    for k in range(len(panel)):
        row = panel.iloc[k]
        if pd.isna(row["y"]) or row[FEATURES].isna().any():
            continue
        past = panel.iloc[:k]
        if n_train(past) < MIN_TRAIN:
            continue
        pred_change = float(row[FEATURES].to_numpy(dtype=float) @ fit(past))
        errs[panel.index[k]] = pred_change - float(row["y"])
    return pd.Series(errs, dtype=float)
