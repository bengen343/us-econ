"""Compute the production AAA next-day forecast (symmetric RBOB ECM).

Read-only. Fits the cointegration + short-run ECM on the long EIA weekly retail
history (AAA's analog) and applies it to the latest AAA level + latest RBOB
settle to produce a one-day-ahead AAA regular forecast, per the hybrid design in
the research harness.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from google.cloud import bigquery

from forecasts.aaa_gasoline.next_day import data, model
from forecasts.aaa_gasoline.next_day.harness import build_weekly_panel
from forecasts.aaa_gasoline.next_day.production import config as cfg


@dataclass(frozen=True)
class Forecast:
    target: str
    target_date: date
    as_of_date: date
    horizon_days: int
    value: float
    value_rounded: float
    anchor_price: float
    rbob_price: float
    equilibrium_price: float
    expected_weekly_move: float
    model_version: str
    units: str
    n_train: int


def compute(client: bigquery.Client) -> list[Forecast]:
    """Return the next-day AAA regular forecast (a single row), or [] if inputs
    are not yet available."""
    eia_retail = data.pull_eia_retail_weekly(client)
    futures = data.pull_futures_daily(client)
    aaa = data.pull_aaa_regular(client)

    rbob_daily = futures["rbob"].dropna() if "rbob" in futures else futures.iloc[:0]
    if aaa.empty or rbob_daily.empty or eia_retail.empty:
        return []

    panel = build_weekly_panel(eia_retail, futures)
    if panel.empty:
        return []

    spec = next(s for s in model.SPECS if s.name == cfg.SPEC_NAME)
    anchor = float(aaa.iloc[-1])
    as_of = aaa.index[-1].date()
    rbob = float(rbob_daily.iloc[-1])

    nd = model.next_day_forecast(panel, spec, anchor, rbob, cfg.TRADING_DAYS_PER_WEEK)

    return [
        Forecast(
            target=cfg.TARGET,
            target_date=as_of + timedelta(days=1),
            as_of_date=as_of,
            horizon_days=1,
            value=nd.next_day,
            value_rounded=round(nd.next_day, 3),
            anchor_price=anchor,
            rbob_price=rbob,
            equilibrium_price=nd.equilibrium,
            expected_weekly_move=nd.weekly_move,
            model_version=cfg.MODEL_VERSION,
            units=cfg.UNITS,
            n_train=nd.n_train,
        )
    ]
