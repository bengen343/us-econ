"""Shared model code for the CPI-shelter forecast (research harness + production).

Shelter is the opposite problem from gasoline/eggs: no contemporaneous
high-frequency input exists, and the series is dominated by persistence (long
leases + the CPI's 6-month sampling window smooth everything). The bake-off
(harness.py) confirmed the literature: the plain TRAILING 6-MONTH MEAN of the
SA m/m beats every fitted spec on both test windows (m/m RMSE 0.089 vs 0.110
for carry-forward on 2010-2026 COVID-masked origins, and best again on the
2021+ window), and ZORI market-rent features add nothing at h=1 -- their
documented 8-14 month lead is entirely embodied in the trailing mean by the
time it matters one month out. So production is deterministic: no fitting,
mirroring how the CPI dms forecast treats its persistent components.

Feature dating: at the origin (just before the mid-(M+1) CPI release) the CPI
is published through M-1; the trailing mean uses months M-6..M-1.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

TRAIL_MONTHS = 6
# Tolerate publication gaps (the 2025 appropriations lapse left October -- and
# hence two m/m changes -- unpublished). Safe because the series is SA, so a
# mean over the available months isn't seasonally biased; same convention as
# the CPI dms harness (min_periods=9 of 12).
MIN_TRAIL = 4


def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Feature panel from monthly ``sh_idx`` (+ research-only ``oer_idx``,
    ``rent_idx``, ``zori``). Every regressor is dated <= M-1."""
    p = pd.DataFrame(index=panel.index)
    p["sh_idx"] = panel["sh_idx"]
    p["y"] = np.log(panel["sh_idx"]).diff()  # target: Delta-log shelter SA, month M

    # Own persistence (published through M-1).
    p["dsh_1"] = p["y"].shift(1)
    p["dsh_2"] = p["y"].shift(2)
    p["dsh_12"] = p["y"].shift(12)
    p["trail3"] = p["y"].shift(1).rolling(3).mean()
    p["trail6"] = p["y"].shift(1).rolling(TRAIL_MONTHS, min_periods=MIN_TRAIL).mean()
    p["trail12"] = p["y"].shift(1).rolling(12).mean()

    # Research-only features (harness specs); absent columns are skipped.
    if "oer_idx" in panel:
        p["oer_1"] = np.log(panel["oer_idx"]).diff().shift(1)
    if "rent_idx" in panel:
        p["rent_1"] = np.log(panel["rent_idx"]).diff().shift(1)
    if "zori" in panel:
        dz = np.log(panel["zori"]).diff()
        p["zori_1"] = dz.shift(1)
        p["zori_6"] = dz.shift(6)
        p["zori_12"] = dz.shift(12)
        p["zori_trail12"] = dz.shift(1).rolling(12).mean()
        # Catch-up gap: trailing-12 market-rent growth minus trailing-12
        # shelter growth, as of M-1 -- the literature's convergence term.
        p["zori_gap"] = p["zori_trail12"] - p["trail12"]

    # Expanding calendar-month mean of the target (PIT; small for an SA series).
    p["seas"] = p["y"].groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    return p


@dataclass(frozen=True)
class ShelterForecast:
    target_month: pd.Timestamp
    level: float  # SA index points
    mm_pct: float  # m/m percent change (SA)
    n_train: int  # months in the trailing mean


def forecast_next(panel: pd.DataFrame) -> ShelterForecast | None:
    """Trailing-6 nowcast of the next CPI month (deterministic, no fitting).

    The target month is the first month after the last published SA index.
    Requires at least MIN_TRAIL of the 6 trailing m/m changes (see MIN_TRAIL
    for why a partial window is tolerable on an SA series).
    """
    last_cpi = panel["sh_idx"].last_valid_index()
    if last_cpi is None:
        return None
    target = last_cpi + pd.offsets.MonthBegin(1)

    y = np.log(panel["sh_idx"].sort_index()).diff()
    tail = y.reindex(pd.date_range(end=last_cpi, periods=TRAIL_MONTHS, freq="MS")).dropna()
    if len(tail) < MIN_TRAIL:
        return None

    yhat = float(tail.mean())
    last_level = float(panel.at[last_cpi, "sh_idx"])
    return ShelterForecast(
        target_month=target,
        level=last_level * float(np.exp(yhat)),
        mm_pct=float(np.expm1(yhat) * 100),
        n_train=len(tail),
    )
