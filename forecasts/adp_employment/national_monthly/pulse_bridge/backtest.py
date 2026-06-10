r"""Read-only validation for the Pulse-bridge model.

Three reports:
  1. LEAVE-ONE-OUT full-month accuracy of the calibrated bridge vs a naive
     (b=1) bridge and the RW prior, over months with near-complete Pulse.
  2. WEEKLY REVISION TRAJECTORY — how the forecast for a target month evolves
     Tuesday-by-Tuesday as Pulse weeks land (demonstrates the live behaviour).
  3. The current LIVE forecast (as_of = today) with all components.

Run:
  .\.venv\Scripts\python.exe -m forecasts.adp_employment.national_monthly.pulse_bridge.backtest

Data caveat: only 2 Pulse vintages exist (from 2026-04-28). The trajectory
report therefore uses latest-vintage values with a *modelled* 17-day publication
lag (inter-vintage revisions ignored) to illustrate the week-by-week ramp; it is
labelled approximate. Accuracy numbers use the full latest vintage.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.adp_employment.national_monthly.pulse_bridge import config as cfg
from forecasts.adp_employment.national_monthly.pulse_bridge import data, model


def _complete_month_pairs(monthly: pd.DataFrame, pulse: pd.DataFrame):
    """(month, implied, headline) for post-floor months with >= min completeness."""
    hl = model.headline_series(monthly)
    floor = pd.Timestamp(cfg.CALIB_FLOOR_MONTH)
    out = []
    for month, y in hl.items():
        if month < floor:
            continue
        run_rate, obs = model.month_pulse(pulse, month)
        exp = model.expected_weeks(month)
        if obs and exp and obs / exp >= cfg.CALIB_MIN_COMPLETENESS:
            out.append((month, run_rate * exp, float(y)))
    return out


def _shrunk_pooled(pairs, completeness: float = 1.0) -> float:
    """Mirrors model.calibrate_scale's maturity-decayed shrinkage. The LOO
    report scores full months (completeness 1.0 -> the raw pooled ratio)."""
    xs = np.array([p[1] for p in pairs], float)
    ys = np.array([p[2] for p in pairs], float)
    if len(xs) == 0 or xs.sum() == 0:
        return cfg.CALIB_B0_PRIOR
    xbar = xs.mean()
    k = cfg.CALIB_PSEUDO_COUNT * max(0.0, 1.0 - completeness)
    b = (ys.sum() + k * cfg.CALIB_B0_PRIOR * xbar) / (xs.sum() + k * xbar)
    return float(np.clip(b, *cfg.CALIB_SCALE_BOUNDS))


def report_loo(monthly: pd.DataFrame, pulse: pd.DataFrame) -> None:
    pairs = _complete_month_pairs(monthly, pulse)
    print("=" * 92)
    print(f"LEAVE-ONE-OUT FULL-MONTH BRIDGE  (n={len(pairs)} complete-Pulse months)")
    print("=" * 92)
    if len(pairs) < 2:
        print("  Not enough complete-Pulse months to leave-one-out yet.")
        return
    hl = model.headline_series(monthly)
    print(f"  {'month':<10}{'headline':>11}{'implied':>11}{'b_LOO':>8}"
          f"{'bridge':>11}{'naive':>11}{'prior(RW)':>11}")
    rows = []
    for i, (month, implied, y) in enumerate(pairs):
        others = pairs[:i] + pairs[i + 1:]
        b = _shrunk_pooled(others)
        bridge = b * implied
        naive = implied
        prior = float(hl.loc[:month].iloc[-2]) if len(hl.loc[:month]) >= 2 else np.nan
        rows.append((y, bridge, naive, prior))
        print(f"  {month.date()!s:<10}{y:>11,.0f}{implied:>11,.0f}{b:>8.3f}"
              f"{bridge:>11,.0f}{naive:>11,.0f}{prior:>11,.0f}")
    arr = np.array(rows, float)
    y = arr[:, 0]
    print("\n  MAE:  "
          f"bridge={np.mean(np.abs(arr[:,1]-y)):>9,.0f}   "
          f"naive(b=1)={np.mean(np.abs(arr[:,2]-y)):>9,.0f}   "
          f"prior(RW)={np.nanmean(np.abs(arr[:,3]-y)):>9,.0f}")
    within = np.mean(np.abs(arr[:, 1] - y) <= 12_500) * 100
    print(f"  bridge within +/-12.5k: {within:.0f}%   "
          f"(goal MAE <= 12,500; n is tiny — directional read only)")


def report_trajectory(monthly: pd.DataFrame, pulse: pd.DataFrame,
                      target: pd.Timestamp) -> None:
    """Approximate week-by-week evolution of the forecast for `target`."""
    print("\n" + "=" * 92)
    print(f"WEEKLY REVISION TRAJECTORY for {target.date()}  "
          f"(approx: latest-vintage values + modelled {cfg.PULSE_LAG_DAYS}d lag)")
    print("=" * 92)
    release = data.release_date_for(target)
    # Candidate refresh Tuesdays from the month start up to (not incl) release.
    start = target
    tuesdays = pd.date_range(start, pd.Timestamp(release), freq="W-TUE")
    print(f"  released {release}; target is live for refreshes below\n")
    print(f"  {'as_of(Tue)':<12}{'wks':>4}{'compl':>7}{'run_rate':>10}"
          f"{'implied':>11}{'pulse_pt':>11}{'prior':>10}{'w':>6}{'FORECAST':>12}")
    actual = model.headline_series(monthly)
    actual_val = actual.get(target, np.nan)
    for tue in tuesdays:
        as_of = tue.date()
        if as_of >= release:
            break
        # Approx PIT: monthly released by as_of; pulse weeks published by as_of.
        m = monthly[monthly["month"].apply(
            lambda mm, _a=as_of: data.release_date_for(mm) <= _a)]
        published = tue - pd.Timedelta(days=cfg.PULSE_LAG_DAYS)
        p = pulse[pulse["week_ending"] <= published]
        if m.empty:
            continue
        fc = model.forecast(m, p)
        if fc.target_month != target:
            continue
        print(f"  {as_of!s:<12}{fc.pulse_weeks_used:>4}{fc.blend_weight:>7.2f}"
              f"{fc.pulse_run_rate:>10,.0f}{fc.pulse_implied:>11,.0f}"
              f"{fc.pulse_component:>11,.0f}{fc.prior_component:>10,.0f}"
              f"{fc.blend_weight:>6.2f}{fc.headline_forecast:>12,.0f}")
    if pd.notna(actual_val):
        print(f"\n  actual {target.date()} headline (latest vintage): {actual_val:>+,.0f}")


def report_live(monthly: pd.DataFrame, pulse: pd.DataFrame) -> None:
    fc = model.forecast(monthly, pulse)
    print("\n" + "=" * 92)
    print(f"LIVE FORECAST  ->  {fc.target_month.date()} headline "
          f"(released ~{data.release_date_for(fc.target_month)})")
    print("=" * 92)
    print(f"  headline_forecast : {fc.headline_forecast:>+,.0f}")
    print(f"  prior (RW)        : {fc.prior_component:>+,.0f}")
    print(f"  pulse component   : {fc.pulse_component:>+,.0f}  "
          f"(b={fc.calib_scale:.3f} raw={fc.calib_raw_scale:.3f} "
          f"on {fc.calib_n_months} months)")
    print(f"  implied / run_rate: {fc.pulse_implied:>+,.0f} / {fc.pulse_run_rate:,.0f}")
    print(f"  pulse weeks used  : {fc.pulse_weeks_used} / {fc.expected_weeks} "
          f"(completeness {fc.blend_weight:.2f}); latest week "
          f"{fc.latest_pulse_week.date() if fc.latest_pulse_week is not None else 'n/a'}")


def run() -> None:
    print("Pulling BigQuery inputs (read-only, latest vintage)...")
    monthly = data.pull_monthly()
    pulse = data.pull_pulse()
    report_loo(monthly, pulse)
    # Most recent complete-Pulse month for a full trajectory illustration.
    pairs = _complete_month_pairs(monthly, pulse)
    if pairs:
        report_trajectory(monthly, pulse, pairs[-1][0])
    report_live(monthly, pulse)


if __name__ == "__main__":
    run()
