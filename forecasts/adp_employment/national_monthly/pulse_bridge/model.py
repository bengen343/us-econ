"""Pure model logic for the ADP headline Pulse-bridge nowcast.

No I/O. Given a monthly SA-level frame and a weekly Pulse frame (already
vintage-/as-of-filtered by data.py), produce the forecast for the next
unreleased month plus every component, so the caller can persist a fully
auditable revision row.

headline(M)        = ner_sa(M) - ner_sa(M-1)                 (the published figure)
run_rate(M)        = mean Pulse 4wk-MA over weeks ending in M
implied(M)         = run_rate(M) * expected_weeks(M)
scale b            = shrunk pooled ratio  headline/implied  over complete months
pulse_point(M)     = b * implied(M)
prior(M)           = last released headline, shrunk toward its trailing mean (RW)
completeness w     = (observed weeks / expected weeks) ** gamma
forecast(M)        = w * pulse_point(M) + (1 - w) * prior(M)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from forecasts.adp_employment.national_monthly.pulse_bridge import config as cfg


def expected_weeks(month: pd.Timestamp) -> int:
    """Number of Saturday week-endings in the calendar month (the Pulse cadence)."""
    start = month
    end = month + pd.offsets.MonthEnd(0)
    days = pd.date_range(start, end, freq="D")
    return int((days.weekday == 5).sum())  # Saturday == 5


def headline_series(monthly: pd.DataFrame) -> pd.Series:
    """MoM change in the SA level, indexed by month. First month dropped (NaN)."""
    s = monthly.set_index("month").sort_index()["ner_sa"].diff()
    return s.dropna()


def month_pulse(pulse: pd.DataFrame, month: pd.Timestamp) -> tuple[float, int]:
    """(run_rate, observed_weeks) for Pulse weeks ending within calendar `month`."""
    end = month + pd.offsets.MonthEnd(0)
    wk = pulse[(pulse["week_ending"] >= month) & (pulse["week_ending"] <= end)]
    if wk.empty:
        return float("nan"), 0
    return float(wk["pulse"].mean()), int(len(wk))


def calibrate_scale(monthly: pd.DataFrame, pulse: pd.DataFrame
                    ) -> tuple[float, int, float]:
    """Shrunk pooled bridge scale b. Returns (b, n_calibration_months, raw_b).

    Uses only months with an observed headline and >= CALIB_MIN_COMPLETENESS of
    their weeks present (so run_rate is a clean full-month estimate). Pooled
    ratio Sum(y)/Sum(implied) is magnitude-weighted (robust to small-implied
    months); shrunk toward CALIB_B0_PRIOR with CALIB_PSEUDO_COUNT synthetic
    months, then clamped to CALIB_SCALE_BOUNDS.
    """
    hl = headline_series(monthly)
    xs, ys = [], []
    for month, y in hl.items():
        run_rate, obs = month_pulse(pulse, month)
        exp = expected_weeks(month)
        if obs == 0 or exp == 0:
            continue
        if obs / exp < cfg.CALIB_MIN_COMPLETENESS:
            continue
        xs.append(run_rate * exp)
        ys.append(float(y))

    n = len(xs)
    if n == 0:
        return cfg.CALIB_B0_PRIOR, 0, cfg.CALIB_B0_PRIOR

    sx, sy = float(np.sum(xs)), float(np.sum(ys))
    raw_b = sy / sx if sx != 0 else cfg.CALIB_B0_PRIOR
    xbar = sx / n
    k = cfg.CALIB_PSEUDO_COUNT
    b = (sy + k * cfg.CALIB_B0_PRIOR * xbar) / (sx + k * xbar)
    lo, hi = cfg.CALIB_SCALE_BOUNDS
    return float(np.clip(b, lo, hi)), n, float(raw_b)


def rw_prior(monthly: pd.DataFrame) -> float:
    """Last released headline shrunk toward the trailing-mean of the last few."""
    hl = headline_series(monthly)
    last = float(hl.iloc[-1])
    trail = float(hl.iloc[-cfg.PRIOR_TRAIL_MONTHS:].mean())
    return cfg.PRIOR_SHRINK * last + (1.0 - cfg.PRIOR_SHRINK) * trail


@dataclass
class Forecast:
    target_month: pd.Timestamp
    headline_forecast: float
    prior_component: float
    pulse_component: float        # b * implied (NaN if no Pulse weeks yet)
    blend_weight: float           # w in [0, 1]
    pulse_run_rate: float
    pulse_implied: float
    pulse_weeks_used: int
    expected_weeks: int
    calib_scale: float
    calib_raw_scale: float
    calib_n_months: int
    latest_pulse_week: pd.Timestamp | None
    audit: dict = field(default_factory=dict)


def forecast(monthly: pd.DataFrame, pulse: pd.DataFrame) -> Forecast:
    """Forecast the next unreleased month's headline from the current data."""
    if monthly.empty:
        raise RuntimeError("no monthly SA-level data available")
    target = monthly["month"].max() + pd.offsets.MonthBegin(1)

    run_rate, obs = month_pulse(pulse, target)
    exp = expected_weeks(target)
    comp = min(obs / exp, 1.0) if exp > 0 else 0.0
    w = comp ** cfg.BLEND_GAMMA

    b, n_cal, raw_b = calibrate_scale(monthly, pulse)
    prior = rw_prior(monthly)

    if obs == 0:
        implied = float("nan")
        pulse_point = float("nan")
        w = 0.0
        point = prior
    else:
        implied = run_rate * exp
        pulse_point = b * implied
        point = w * pulse_point + (1.0 - w) * prior

    latest_wk = pulse["week_ending"].max() if not pulse.empty else None
    return Forecast(
        target_month=target,
        headline_forecast=float(point),
        prior_component=float(prior),
        pulse_component=float(pulse_point),
        blend_weight=float(w),
        pulse_run_rate=float(run_rate) if obs else float("nan"),
        pulse_implied=float(implied),
        pulse_weeks_used=int(obs),
        expected_weeks=int(exp),
        calib_scale=float(b),
        calib_raw_scale=float(raw_b),
        calib_n_months=int(n_cal),
        latest_pulse_week=latest_wk,
    )
