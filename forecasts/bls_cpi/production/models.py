"""Live CPI nowcast for the next-to-be-released month, deterministic DMS model.

Reuses the research data layer and reconstruction (``../data``, ``../dms``) so
production and backtest stay in lockstep. The reconstruction is deterministic
(no fitting): it rebuilds the headline/core m/m from price-updated RI-weighted
component nowcasts (core/food trailing averages + the high-frequency gasoline
nowcast), and chains the SA m/m onto the SA index for the y/y. The target is the
month after the last published CPI — the one the imminent release will report —
nowcast from the complete fuel prices for that month plus CPI prints through M-1.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from forecasts.bls_cpi import data
from forecasts.bls_cpi.dms import harness as dms
from forecasts.bls_cpi.dms import panel as panel_mod
from forecasts.bls_cpi.production import config as cfg


@dataclass
class ForecastRow:
    target: str
    model_version: str
    target_month: pd.Timestamp
    value: float
    value_rounded: float
    units: str
    n_train: int  # months of CPI history feeding the reconstruction


def compute(client) -> list[ForecastRow]:
    panel = panel_mod.build_panel(data.pull_cpi(client), data.pull_eia_monthly(client))
    weights, weight_year = data.pull_cpi_weights(client)
    f = dms.build_forecasts(panel, weights, weight_year)

    last_actual = panel["all_sa_mm"].last_valid_index()
    target_month = last_actual + pd.offsets.MonthBegin(1)
    n = int(panel.loc[:last_actual, "all_sa_mm"].notna().sum())

    if target_month not in f.index:
        return []

    rows: list[ForecastRow] = []
    for target, units in cfg.TARGET_UNITS.items():
        value = f.at[target_month, target]
        if pd.isna(value):
            continue
        rows.append(
            ForecastRow(
                target=target,
                model_version=cfg.MODEL_VERSION,
                target_month=target_month,
                value=round(float(value), 2),
                value_rounded=round(float(value), 1),  # BLS publishes to 0.1pp
                units=units,
                n_train=n,
            )
        )
    return rows
