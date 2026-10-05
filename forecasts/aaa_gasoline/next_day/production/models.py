"""Compute the production AAA next-day forecasts (both active model versions).

Read-only. Builds the point-in-time daily panel (``..daily``): for every AAA day,
the seasonal-EC ECM (fit on the long EIA weekly retail history, AAA's analog) is
evaluated at that day's AAA level and the last settled RBOB, alongside AAA
momentum and RBOB daily changes. The latest row feeds both models:

* ``ecm_seas_mom_v1``: the row's ECM step blended with the latest AAA
  day-over-day change (see ``config`` for the rationale and weights).
* ``daily_ar_rbob_v1``: a regression of next-day AAA change on the panel's
  features, fit on every earlier day with a known outcome.

Inputs are restricted to data dated before the as_of (AAA) date, so the morning
run never reads an unsettled same-day RBOB quote. Each model's predictive
distribution is a Student-t whose sd is the RMSE of that model's own
out-of-sample daily errors.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from google.cloud import bigquery

from forecasts.aaa_gasoline.next_day import daily, data, model
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


def _rmse_sigma(errors: pd.Series, fallback: float, model_version: str) -> float:
    """RMSE of out-of-sample errors once SIGMA_MIN_ERRORS exist, else `fallback`."""
    errors = errors.dropna()
    if len(errors) < cfg.SIGMA_MIN_ERRORS:
        _log.warning(
            "too few scored errors for a calibrated sigma; using weekly-ECM fallback",
            extra={"extras": {"model_version": model_version, "n_errors": len(errors)}},
        )
        return fallback
    return float(math.sqrt(float((errors**2).mean())))


def _forecast_row(
    value: float,
    model_version: str,
    sigma: float,
    last: pd.Series,
    as_of: date,
    n_train: int,
) -> Forecast:
    return Forecast(
        target=cfg.TARGET,
        target_date=as_of + timedelta(days=1),
        as_of_date=as_of,
        horizon_days=1,
        value=value,
        value_rounded=round(value, 3),
        anchor_price=float(last["anchor"]),
        rbob_price=float(last["rbob"]),
        equilibrium_price=float(last["equilibrium"]),
        expected_weekly_move=float(last["weekly_move"]),
        sigma_daily=sigma,
        distribution=model.predictive_distribution(
            value, sigma, cfg.DIST_BUCKET_WIDTH, cfg.DIST_SPAN_SIGMAS, df=cfg.DIST_T_DF
        ),
        model_version=model_version,
        units=cfg.UNITS,
        n_train=n_train,
    )


def compute(client: bigquery.Client) -> list[Forecast]:
    """Return the next-day AAA regular forecasts (one row per active model
    version), or [] if inputs are not yet available."""
    eia_retail = data.pull_eia_retail_weekly(client)
    futures = data.pull_futures_daily(client)
    aaa = data.pull_aaa_regular(client)
    if aaa.empty or futures.empty or eia_retail.empty:
        return []

    as_of_ts = pd.Timestamp(aaa.index[-1])
    as_of = as_of_ts.date()
    spec_b = next(s for s in model.SPECS if s.name == cfg.SPEC_NAME_BLEND)
    panel = daily.build_panel(aaa, futures, eia_retail, spec_b, cfg.TRADING_DAYS_PER_WEEK)
    if panel.empty or panel.index[-1] != as_of_ts:
        _log.warning("no daily panel row for the as_of date; skipping")
        return []
    last = panel.iloc[-1]

    # Cold-start fallback sigma: the weekly-ECM walk-forward estimate, on the same
    # point-in-time inputs as the panel's latest row.
    cutoff = as_of_ts - pd.Timedelta(days=1)
    eia_retail.index = pd.to_datetime(eia_retail.index)
    weekly = build_weekly_panel(
        eia_retail[eia_retail.index <= cutoff], futures[futures.index <= cutoff]
    )
    _, fallback_sigma = model.forecast_error_sigma(
        weekly, spec_b, pd.Timestamp(cfg.SIGMA_TEST_START), cfg.TRADING_DAYS_PER_WEEK
    )

    forecasts: list[Forecast] = []

    # ---- v2: seasonal-EC ECM drift blended with daily AAA momentum --------- #
    momentum = _aaa_momentum(aaa)
    if momentum is None:
        _log.warning(
            "blend model skipped: no usable AAA momentum",
            extra={"extras": {"model_version": cfg.MODEL_VERSION_BLEND, "n_aaa": len(aaa)}},
        )
    else:
        value_b = (
            float(last["anchor"])
            + cfg.ECM_WEIGHT * float(last["ecm_step"])
            + cfg.MOMENTUM_WEIGHT * momentum
        )
        sigma_b = _rmse_sigma(
            data.pull_live_errors(cfg.MODEL_VERSION_BLEND, as_of_ts, client),
            fallback_sigma,
            cfg.MODEL_VERSION_BLEND,
        )
        _log.info(
            "blend components",
            extra={
                "extras": {
                    "model_version": cfg.MODEL_VERSION_BLEND,
                    "ecm_step": round(float(last["ecm_step"]), 4),
                    "momentum": round(momentum, 4),
                    "equilibrium_seasonal": round(float(last["equilibrium"]), 3),
                    "sigma": round(sigma_b, 4),
                }
            },
        )
        forecasts.append(
            _forecast_row(
                value_b, cfg.MODEL_VERSION_BLEND, sigma_b, last, as_of, int(last["n_weekly"])
            )
        )

    # ---- v3: daily regression on AAA's own history -------------------------- #
    n_train = daily.n_train(panel)
    if last[daily.FEATURES].isna().any() or n_train < daily.MIN_TRAIN:
        _log.warning(
            "daily model skipped: incomplete features or too little history",
            extra={
                "extras": {
                    "model_version": cfg.MODEL_VERSION_DAILY,
                    "n_train": n_train,
                    "missing": [f for f in daily.FEATURES if pd.isna(last[f])],
                }
            },
        )
    else:
        coef = daily.fit(panel)
        x = last[daily.FEATURES].to_numpy(dtype=float)
        value_d = float(last["anchor"]) + float(x @ coef)
        sigma_d = _rmse_sigma(daily.pit_errors(panel), fallback_sigma, cfg.MODEL_VERSION_DAILY)
        _log.info(
            "daily model components",
            extra={
                "extras": {
                    "model_version": cfg.MODEL_VERSION_DAILY,
                    "coef": dict(zip(daily.FEATURES, coef.round(4).tolist(), strict=True)),
                    "features": dict(zip(daily.FEATURES, x.round(4).tolist(), strict=True)),
                    "n_train": n_train,
                    "sigma": round(sigma_d, 4),
                }
            },
        )
        forecasts.append(
            _forecast_row(value_d, cfg.MODEL_VERSION_DAILY, sigma_d, last, as_of, n_train)
        )

    return forecasts

