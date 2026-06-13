r"""Walk-forward research harness for the RCP Friday-average forecast.

Read-only, offline. Pulls the snapshot history from BigQuery (the same data layer
production uses), reconstructs the as-of window at every capture day, and backtests
the structural-blend forecast against carry-forward and drift baselines at each
horizon h=1..6 (origin = the prior Thu .. prior Sat). Prints the per-horizon
scorecard that backs README.md.

Origins are capture days (window membership known); target = the next Friday
1..6 days out whose published average is recorded. Because the model's point
forecast averages many Monte-Carlo paths, the scorecard is stable run-to-run but
not bit-identical; a fixed RNG seed keeps it reproducible.

Run: .\.venv\Scripts\python.exe -m forecasts.rcp_potus_approval.friday_average.harness
"""

from __future__ import annotations

import random
import statistics
from collections import defaultdict
from datetime import timedelta

from google.cloud import bigquery

from forecasts.rcp_potus_approval.friday_average import data, model

SEED = 20260613
WARMUP_DAYS = 28  # need some release history before the first origin


def _score(errs):
    ae = [abs(e) for e in errs]
    return {
        "n": len(errs),
        "MAE": statistics.mean(ae),
        "RMSE": statistics.mean(e * e for e in errs) ** 0.5,
        "bias": statistics.mean(errs),
    }


def run() -> None:
    client = bigquery.Client(project=data.PROJECT)
    print("Pulling rcp_potus_approval.polls (read-only)...")
    windows, truth, releases = data.load_all(client)
    fridays = {d for d in truth if d.weekday() == 4}
    print(f"windows {len(windows)} days, truth {len(truth)} days, "
          f"pollsters {len(releases)}, Fridays {len(fridays)}")

    start = min(windows) + timedelta(days=WARMUP_DAYS)
    origins = []
    for D in sorted(windows):
        if D < start:
            continue
        for h in range(1, 7):
            F = D + timedelta(days=h)
            if F in fridays and F in truth:
                origins.append((D, F, h))
                break
    print(f"backtest origins: {len(origins)}\n")

    rng = random.Random(SEED)
    by_h = defaultdict(lambda: defaultdict(list))
    for D, F, h in origins:
        y = truth[F]
        params = model.Params(releases, truth, D)
        drift = model.local_drift(truth, D)
        cf = truth[D]
        m_nd, _, _ = model.simulate(D, F, windows[D], params, drift, use_drift=False, rng=rng)
        wc = model.carry_weight(h)
        blend = wc * (cf + 0.5 * drift * h) + (1 - wc) * m_nd
        by_h[h]["carry_fwd"].append(cf - y)
        by_h[h]["drift_half"].append((cf + 0.5 * drift * h) - y)
        by_h[h]["structural"].append(m_nd - y)
        by_h[h]["blend"].append(blend - y)

    names = ["carry_fwd", "drift_half", "structural", "blend"]
    print("=" * 74)
    print("WALK-FORWARD BAKE-OFF  (error = pred - actual)")
    print("=" * 74)
    for h in range(1, 7):
        if h not in by_h:
            continue
        print(f"\nhorizon h={h}  (n={len(by_h[h]['carry_fwd'])})")
        for nm in names:
            s = _score(by_h[h][nm])
            print(f"   {nm:<12} MAE={s['MAE']:.3f}  RMSE={s['RMSE']:.3f}  bias={s['bias']:+.3f}")

    print("\n" + "=" * 74)
    print("HORIZON-AVERAGED (equal weight per h=1..6)")
    print("=" * 74)
    for nm in names:
        hs = [h for h in range(1, 7) if h in by_h]
        mae = statistics.mean(_score(by_h[h][nm])["MAE"] for h in hs)
        rmse = statistics.mean(_score(by_h[h][nm])["RMSE"] for h in hs)
        print(f"   {nm:<12} MAE={mae:.3f}  RMSE={rmse:.3f}")

    # markdown table for the README
    print("\nREADME table (RMSE):")
    print("| h | carry-fwd | blend |")
    print("|---|-----------|-------|")
    for h in range(1, 7):
        if h in by_h:
            print(f"| {h} | {_score(by_h[h]['carry_fwd'])['RMSE']:.3f} | "
                  f"{_score(by_h[h]['blend'])['RMSE']:.3f} |")

    _release_calibration(windows, truth, releases)


def _release_calibration(windows, truth, releases) -> None:
    """Calibration + Brier skill of the per-pollster Friday-release prediction:
    for each capture-day Friday, predict P(release) at the nearest capture origin
    and compare to whether the house actually released (a poll dated) that Friday."""
    capdays = set(windows)
    fridays = sorted(d for d in truth if d.weekday() == 4 and d in capdays)
    released_on = defaultdict(set)
    for pl, recs in releases.items():
        for (rel, end, v) in recs:
            released_on[rel].add(pl)

    rng = random.Random(SEED)
    pairs = []
    for F in fridays:
        for h in range(1, 7):
            D = F - timedelta(days=h)
            if D not in capdays or D < min(capdays) + timedelta(days=WARMUP_DAYS):
                continue
            params = model.Params(releases, truth, D)
            _, _, fri = model.simulate(D, F, windows[D], params, model.local_drift(truth, D), rng=rng)
            movers = params.active | {p for (p, e, v) in windows[D]}
            actual = released_on.get(F, set())
            for pl in movers:
                pairs.append((fri.get(pl, 0) / model.N_SIMS, 1 if pl in actual else 0))
            break
    if not pairs:
        return
    base = statistics.mean(y for _, y in pairs)
    brier = statistics.mean((p - y) ** 2 for p, y in pairs)
    brier_base = statistics.mean((base - y) ** 2 for _, y in pairs)
    print("\n" + "=" * 74)
    print(f"FRIDAY-RELEASE PREDICTION  ({len(pairs)} house-Fridays, base rate {base:.2f})")
    print("=" * 74)
    print("  pred-bin    n   mean_pred  obs_freq")
    for lo, hi in [(0, .05), (.05, .15), (.15, .3), (.3, .5), (.5, .75), (.75, 1.01)]:
        sel = [(p, y) for (p, y) in pairs if lo <= p < hi]
        if sel:
            print(f"  [{lo:.2f},{hi:.2f}) {len(sel):4d}    {statistics.mean(p for p, _ in sel):.2f}"
                  f"      {statistics.mean(y for _, y in sel):.2f}")
    print(f"  Brier {brier:.4f} vs base {brier_base:.4f}  ->  skill {1 - brier / brier_base:+.0%}")


if __name__ == "__main__":
    run()
