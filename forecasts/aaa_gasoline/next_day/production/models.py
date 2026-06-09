"""Compute the production AAA next-day forecasts (both model versions).

Read-only. Fits the cointegration + short-run ECM on the long EIA weekly retail
history (AAA's analog) and applies it to the latest AAA level + latest RBOB
settle to produce one-day-ahead AAA regular forecasts, per the hybrid design in
the research harness:

* ``ecm_sym_rbob_v1``: pure ECM, daily step = expected weekly move / 5.
* ``ecm_seas_mom_v1``: seasonal-EC ECM drift blended with the latest AAA
  day-over-day change (see ``config`` for the rationale and weights).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from google.cloud import bigquery

from forecasts.aaa_gasoline.next_day import data, model
from forecasts.aaa_gasoline.next_day.harness import build_weekly_panel
from forecasts.aaa_gasoline.next_day.production import config as cfg

_log = logging.getLogger(__name__)


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
    sigma_daily: float
    distribution: list[model.DistBucket]
    model_version: str
    units: str
    n_train: int


def _aaa_momentum(aaa: pd.Series) -> float | None:
    """Latest AAA day-over-day change ($/gal per day), or None if unavailable.

    A short scrape gap is tolerated by averaging the change over the gap; beyond
    MOMENTUM_MAX_GAP_DAYS the signal is stale and the blend model is skipped.
    """
    if len(aaa) < 2:
        return None
    gap_days = (aaa.index[-1] - aaa.index[-2]).days
    if gap_days < 1 or gap_days > cfg.MOMENTUM_MAX_GAP_DAYS:
        return None
    return float(aaa.iloc[-1] - aaa.iloc[-2]) / gap_days


def _forecast_row(
    nd: model.NextDay,
    value: float,
    model_version: str,
    daily_sigma: float,
    anchor: float,
    rbob: float,
    as_of: date,
) -> Forecast:
    return Forecast(
        target=cfg.TARGET,
        target_date=as_of + timedelta(days=1),
        as_of_date=as_of,
        horizon_days=1,
        value=value,
        value_rounded=round(value, 3),
        anchor_price=anchor,
        rbob_price=rbob,
        equilibrium_price=nd.equilibrium,
        expected_weekly_move=nd.weekly_move,
        sigma_daily=daily_sigma,
        distribution=model.predictive_distribution(
            value, daily_sigma, cfg.DIST_BUCKET_WIDTH, cfg.DIST_SPAN_SIGMAS
        ),
        model_version=model_version,
        units=cfg.UNITS,
        n_train=nd.n_train,
    )


def compute(client: bigquery.Client) -> list[Forecast]:
    """Return the next-day AAA regular forecasts (one row per model version),
    or [] if inputs are not yet available."""
    eia_retail = data.pull_eia_retail_weekly(client)
    futures = data.pull_futures_daily(client)
    aaa = data.pull_aaa_regular(client)

    rbob_daily = futures["rbob"].dropna() if "rbob" in futures else futures.iloc[:0]
    if aaa.empty or rbob_daily.empty or eia_retail.empty:
        return []

    panel = build_weekly_panel(eia_retail, futures)
    if panel.empty:
        return []

    anchor = float(aaa.iloc[-1])
    as_of = aaa.index[-1].date()
    rbob = float(rbob_daily.iloc[-1])
    sigma_start = pd.Timestamp(cfg.SIGMA_TEST_START)

    # ---- v1: pure symmetric ECM, weekly move / 5 --------------------------- #
    spec = next(s for s in model.SPECS if s.name == cfg.SPEC_NAME)
    nd = model.next_day_forecast(panel, spec, anchor, rbob, cfg.TRADING_DAYS_PER_WEEK)
    _, daily_sigma = model.forecast_error_sigma(
        panel, spec, sigma_start, cfg.TRADING_DAYS_PER_WEEK
    )
    forecasts = [
        _forecast_row(nd, nd.next_day, cfg.MODEL_VERSION, daily_sigma, anchor, rbob, as_of)
    ]

    # ---- v2: seasonal-EC ECM drift blended with daily AAA momentum --------- #
    momentum = _aaa_momentum(aaa)
    if momentum is None:
        _log.warning(
            "blend model skipped: no usable AAA momentum",
            extra={"extras": {"model_version": cfg.MODEL_VERSION_BLEND, "n_aaa": len(aaa)}},
        )
        return forecasts

    spec_b = next(s for s in model.SPECS if s.name == cfg.SPEC_NAME_BLEND)
    nd_b = model.next_day_forecast(
        panel, spec_b, anchor, rbob, cfg.TRADING_DAYS_PER_WEEK, as_of_month=as_of.month
    )
    ecm_step = nd_b.weekly_move / cfg.TRADING_DAYS_PER_WEEK
    value_b = anchor + cfg.ECM_WEIGHT * ecm_step + cfg.MOMENTUM_WEIGHT * momentum
    _, daily_sigma_b = model.forecast_error_sigma(
        panel, spec_b, sigma_start, cfg.TRADING_DAYS_PER_WEEK
    )
    _log.info(
        "blend components",
        extra={
            "extras": {
                "model_version": cfg.MODEL_VERSION_BLEND,
                "ecm_step": round(ecm_step, 4),
                "momentum": round(momentum, 4),
                "equilibrium_seasonal": round(nd_b.equilibrium, 3),
            }
        },
    )
    forecasts.append(
        _forecast_row(nd_b, value_b, cfg.MODEL_VERSION_BLEND, daily_sigma_b, anchor, rbob, as_of)
    )
    return forecasts
