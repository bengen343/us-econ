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
        m_nd, _ = model.simulate(D, F, windows[D], params, drift, use_drift=False, rng=rng)
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


if __name__ == "__main__":
    run()
