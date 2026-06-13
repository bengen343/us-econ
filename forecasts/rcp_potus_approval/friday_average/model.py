"""Structural Monte-Carlo model for the RCP Friday approval-average forecast.

Mechanics (reverse-engineered from 17 months of Wayback membership panels):
  * The published average is the simple mean of the polls currently on the page.
  * It changes only by poll ENTRIES and EXITS. A new poll from a pollster REPLACES
    that pollster's prior poll (deterministic). Old polls are PRUNED with an
    age-rising hazard (no fixed cutoff). The window size is ~stable.
  * Poll values are dominated by pollster "house effects"; within-pollster
    poll-to-poll change is small.

The simulator marks each future day with per-pollster renewal release hazards,
applies replacement + prune, and reports the mean of the surviving window. The
production point forecast is a horizon-weighted blend of drift-corrected
carry-forward (which dominates at short horizons — the average is near a random
walk day to day) and the structural sim (which earns its keep at h>=4):

    forecast = wc*(A_D + 0.5*drift*h) + (1-wc)*struct,   wc = (7-h)/6

See harness.py for the walk-forward bake-off and README.md for the per-horizon
error table. Hyperparameters below are the single source of truth.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

MODEL_VERSION = "struct_blend_v1"

DRIFT_WIN = 14          # trailing days for the local published-average slope
N_SIMS = 500            # Monte-Carlo paths
PRUNE_SCALE = 0.80      # global trim on the prune hazard (calibrates window size ~flat)
DORMANCY_MULT = 3.0     # a pollster idle > this * median_gap is treated inactive
DORMANCY_CAP = 50       # ...but never longer than this many days
FALLBACK_DUE = 0.7      # geometric-fallback pollsters fire only past this * median_gap
NOISE_FLOOR = 0.3       # min per-entry value noise sd
# Release weekday: each house publishes on characteristic weekdays (RMG Fridays,
# Economist/YouGov Tue/Wed, CBS Sundays). The renewal hazard alone spreads a
# release across nearby days; this factor reweights the daily hazard toward a
# house's actual publish weekdays (mostly suppressing the wrong ones). 7*share is
# relative-to-uniform; smoothed and capped so sparse houses aren't over-confident.
WEEKDAY_ALPHA = 0.5     # Laplace smoothing on the per-weekday release share
WEEKDAY_FACTOR_CAP = 2.0
FIRE_CAP = 0.95         # max per-day release probability after weekday reweighting

# Age-based per-day prune hazard (empirical, _prune_hazard.py over 17mo Wayback).
PRUNE_HAZARD = [(9, 0.010), (13, 0.020), (17, 0.057), (21, 0.153),
                (25, 0.238), (29, 0.333), (999, 0.417)]


def prune_p(age: int) -> float:
    for hi, p in PRUNE_HAZARD:
        if age <= hi:
            return p * PRUNE_SCALE
    return PRUNE_HAZARD[-1][1] * PRUNE_SCALE


def carry_weight(h: int) -> float:
    return max(0.0, (7 - h) / 6.0)


def _build_hazard(gaps: list[int]) -> dict[int, float]:
    """Empirical discrete-time renewal hazard h(k) = P(gap==k | gap>=k)."""
    haz = {}
    for k in range(1, max(gaps) + 1):
        ge = sum(1 for g in gaps if g >= k)
        haz[k] = (sum(1 for g in gaps if g == k) / ge) if ge else 1.0
    return haz


class Params:
    """Per-origin model parameters, fit on releases observed on/before D."""

    def __init__(self, releases: dict, truth: dict, D: date):
        self.D = D
        self.A_D = truth.get(D)
        self.house_offset: dict[str, float] = {}
        self.hazard: dict[str, dict[int, float]] = {}
        self.maxgap: dict[str, int] = {}
        self.med_gap: dict[str, float] = {}
        self.last_rel: dict[str, date] = {}
        self.weekday_factor: dict[str, list[float]] = {}
        self.active: set[str] = set()
        self.noise_sd = 1.0
        resid = []
        for pollster, recs in releases.items():
            past = [(r, e, v) for (r, e, v) in recs if r <= D]
            if not past:
                continue
            self.last_rel[pollster] = past[-1][0]
            offs = [v - truth[r] for (r, e, v) in past if r in truth]
            self.house_offset[pollster] = statistics.mean(offs) if offs else 0.0
            wd = [0] * 7
            for (r, e, v) in past:
                wd[r.weekday()] += 1
            tot = len(past)
            self.weekday_factor[pollster] = [
                min(7.0 * (wd[w] + WEEKDAY_ALPHA) / (tot + 7 * WEEKDAY_ALPHA), WEEKDAY_FACTOR_CAP)
                for w in range(7)
            ]
            gaps = [(past[i][0] - past[i - 1][0]).days for i in range(1, len(past))]
            gaps = [g for g in gaps if g >= 1]
            if len(gaps) >= 3:
                self.hazard[pollster] = _build_hazard(gaps)
                self.maxgap[pollster] = max(gaps)
                self.med_gap[pollster] = statistics.median(gaps)
            elif gaps:
                self.med_gap[pollster] = statistics.median(gaps)
            else:
                self.med_gap[pollster] = 28.0
            dormancy = min(DORMANCY_MULT * self.med_gap[pollster], DORMANCY_CAP)
            if (D - past[-1][0]).days <= dormancy:
                self.active.add(pollster)
            for (r, e, v) in past:
                if r in truth:
                    resid.append(v - truth[r] - self.house_offset[pollster])
        if len(resid) > 5:
            self.noise_sd = max(statistics.pstdev(resid), NOISE_FLOOR)

    def release_p(self, pollster: str, days_since: int, weekday: int) -> float:
        if days_since < 1:
            return 0.0
        haz = self.hazard.get(pollster)
        if haz is not None:
            if days_since in haz:
                base = haz[days_since]
            else:
                base = 0.6 if days_since > self.maxgap.get(pollster, 0) else 0.0
        else:
            g = self.med_gap.get(pollster, 28.0)
            base = 0.0 if days_since < FALLBACK_DUE * g else min(1.5 / g, 0.5)
        if base <= 0.0:
            return 0.0
        wf = self.weekday_factor.get(pollster, [1.0] * 7)[weekday]
        return min(base * wf, FIRE_CAP)


def local_drift(truth: dict, D: date, win: int = DRIFT_WIN) -> float:
    a0 = truth.get(D)
    for back in range(win, 4, -1):
        a1 = truth.get(D - timedelta(days=back))
        if a0 is not None and a1 is not None:
            return (a0 - a1) / back
    return 0.0


def simulate(D, F, window, params, drift, n_sims=N_SIMS, use_drift=False, rng=None):
    """Monte-Carlo the window forward from D to F.

    Returns (mean, samples, friday_release_counts) where friday_release_counts[p]
    is the number of paths in which pollster p released a poll on the final day F
    (i.e. P(p releases on the target Friday) = count / n_sims).
    """
    rng = rng or random
    L_D = params.A_D
    horizon = (F - D).days
    base = [(pl, end, val) for (pl, end, val) in window]
    movers = params.active | {pl for (pl, e, v) in base}
    preds = []
    fri_releases: dict[str, int] = {}
    for _ in range(n_sims):
        win = [[pl, end, val] for (pl, end, val) in base]
        last_rel = dict(params.last_rel)
        for (pl, end, val) in base:
            if pl not in last_rel or end > last_rel[pl]:
                last_rel[pl] = end
        for step in range(1, horizon + 1):
            t = D + timedelta(days=step)
            is_final = step == horizon
            L_t = L_D + (drift * step if use_drift else 0.0)
            for pl in movers:
                lr = last_rel.get(pl)
                if lr is None:
                    continue
                if rng.random() < params.release_p(pl, (t - lr).days, t.weekday()):
                    val = L_t + params.house_offset.get(pl, 0.0) + rng.gauss(0, params.noise_sd)
                    win = [row for row in win if row[0] != pl]
                    win.append([pl, t - timedelta(days=1), val])
                    last_rel[pl] = t - timedelta(days=1)
                    if is_final:
                        fri_releases[pl] = fri_releases.get(pl, 0) + 1
            newest = {}
            for row in win:
                newest[row[0]] = max(newest.get(row[0], row[1]), row[1])
            win = [row for row in win
                   if not (row[1] == newest[row[0]] and rng.random() < prune_p((t - row[1]).days))]
        preds.append(statistics.mean(r[2] for r in win))
    return statistics.mean(preds), preds, fri_releases


@dataclass
class PollsterRelease:
    pollster: str
    release_prob: float        # P(a new poll from this house appears on the target Friday)
    expected_approve: float    # modeled approve value if it releases (level + house offset)
    days_since_last: int       # days from its last seen poll to the target Friday
    in_current_window: bool    # already on the page as of the origin


@dataclass
class Forecast:
    target_friday: date
    as_of_date: date
    horizon_days: int
    forecast: float            # headline (horizon blend)
    forecast_rounded: float
    carry_forward: float       # A_D
    drift_corrected_carry: float
    structural_mean: float
    drift_per_day: float
    band_lo: float             # 10th pct of structural sim
    band_hi: float             # 90th pct
    n_window: int
    friday_releases: list = field(default_factory=list)  # [PollsterRelease], desc by prob
    model_version: str = MODEL_VERSION
    components: dict = field(default_factory=dict)


def forecast(windows, truth, releases, as_of: date, target_friday: date,
             n_sims: int = N_SIMS, seed: int | None = None) -> Forecast | None:
    """Produce the blended forecast for ``target_friday`` from the snapshot at
    ``as_of`` (uses the latest window on/before as_of)."""
    D = max((d for d in windows if d <= as_of), default=None)
    if D is None or D not in truth:
        return None
    h = (target_friday - D).days
    if h < 1:
        return None

    params = Params(releases, truth, D)
    drift = local_drift(truth, D)
    rng = random.Random(seed) if seed is not None else random

    m_nd, preds, fri = simulate(D, target_friday, windows[D], params, drift, n_sims=n_sims, rng=rng)
    cf = truth[D]
    cfd = cf + 0.5 * drift * h
    wc = carry_weight(h)
    blend = wc * cfd + (1 - wc) * m_nd
    preds_sorted = sorted(preds)
    lo = preds_sorted[int(0.1 * len(preds_sorted))]
    hi = preds_sorted[int(0.9 * len(preds_sorted))]

    window_pollsters = {pl for (pl, e, v) in windows[D]}
    releases_out = []
    for pl in params.active | window_pollsters:
        prob = fri.get(pl, 0) / n_sims
        if prob <= 0.0:
            continue
        releases_out.append(PollsterRelease(
            pollster=pl,
            release_prob=prob,
            expected_approve=cf + params.house_offset.get(pl, 0.0),
            days_since_last=(target_friday - params.last_rel[pl]).days
            if pl in params.last_rel else None,
            in_current_window=pl in window_pollsters,
        ))
    releases_out.sort(key=lambda r: -r.release_prob)

    return Forecast(
        friday_releases=releases_out,
        target_friday=target_friday,
        as_of_date=D,
        horizon_days=h,
        forecast=blend,
        forecast_rounded=round(blend, 1),
        carry_forward=cf,
        drift_corrected_carry=cfd,
        structural_mean=m_nd,
        drift_per_day=drift,
        band_lo=lo,
        band_hi=hi,
        n_window=len(windows[D]),
        components={"carry_weight": wc},
    )
