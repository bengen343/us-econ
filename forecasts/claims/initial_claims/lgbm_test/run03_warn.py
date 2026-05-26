"""Iteration 3: LGBM + WARN with advance/lag sweep. WARN is aggregated per
effective-week (sum of affected workers). We test:

  * backward lags k>0 (warn_workers[T-k]) — past layoff effective dates as a
    proxy for ongoing layoff momentum;
  * forward shifts k<0 (warn_workers[T+|k|]) — future-effective notices visible
    today because WARN is typically filed 60 days in advance (PIT-loose: assumes
    filings cover the effective week being referenced);
  * rolling means around the optimum to smooth WARN's spiky weekly aggregates.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run03_warn.py
"""

from __future__ import annotations

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()

    # Use train_start=2022-01-01 so this is comparable to iter-2 / iter-4.
    eval_spec = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")

    floor_spec = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    # Sweep of single-lag WARN features. Positive lag = past, negative = future.
    single_lags = [-8, -6, -4, -2, -1, 0, 1, 2, 4, 6, 8, 9, 12, 16]

    variants = [("floor (target only)", floor_spec)]
    for k in single_lags:
        spec = FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [k]})
        sign = "fwd" if k < 0 else ("now" if k == 0 else "back")
        variants.append((f"warn lag {k:+d} ({sign})", spec))

    # Lag bundles / rolling means
    variants.append(("warn lags 4,9 (back-bundle)",
                     FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [4, 9]})))
    variants.append(("warn lags -4,0,4 (span)",
                     FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [-4, 0, 4]})))
    variants.append(("warn roll(4w, lag 2)",
                     FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [], "warn_roll": [(4, 2)]})))
    variants.append(("warn roll(4w, lag 6)",
                     FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [], "warn_roll": [(4, 6)]})))
    variants.append(("warn roll(8w, lag 4) + lag -4",
                     FeatureSpec(**{**floor_spec.__dict__, "warn_lags": [-4], "warn_roll": [(8, 4)]})))

    print("=" * 96)
    print("Iteration 3: LGBM + WARN, advance/lag sweep")
    print(f"Train: {eval_spec.train_start}+   Eval: {eval_spec.eval_start}+")
    print("=" * 96)

    rows = []
    for label, spec in variants:
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, eval_spec, model="lgbm", refit_every=4)
        rows.append({
            "variant": label,
            "n_features": len(feats),
            "mae": r["model_mae"],
            "rmse": r["model_rmse"],
            "bias": r["model_bias"],
        })
        print(fmt_summary(r, label))

    df = pd.DataFrame(rows).sort_values("mae")
    print()
    print("Leaderboard (best first):")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
