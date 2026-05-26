"""Iteration 4: combined LGBM + Trends + WARN.

Uses winners from iter-2 (Trends lag 4) and iter-3 (WARN lag +9 backward, WARN
lag -8 forward). Tests several combinations and also a few small-sweep tweaks
around the joint optimum.

The forward-WARN feature (lag -8) reduces the eval sample by 8 weeks (since the
most recent 8 origins don't have 8-week-future WARN yet). To make those rows
comparable to others, we ALSO score the floor on a 'matched' restricted eval
window so cross-variant MAE differences aren't sample-shift artifacts.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run04_combined.py
"""

from __future__ import annotations

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]

    eval_full = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")
    # When using forward-WARN (lag -8), latest 8 eval weeks drop. Match against floor.
    eval_matched = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01", eval_end="2026-03-21")

    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    # Variants: (label, FeatureSpec, eval_spec)
    variants = [
        ("floor (full sample)",                floor, eval_full),
        ("floor (matched n=90)",               floor, eval_matched),

        # Trends-only winners (rerun for sanity)
        ("trends lag 4",                       FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4]}), eval_full),

        # WARN-only winners
        ("warn lag +9 only",                   FeatureSpec(**{**floor.__dict__, "warn_lags": [9]}), eval_full),
        ("warn lag -8 only (matched)",         FeatureSpec(**{**floor.__dict__, "warn_lags": [-8]}), eval_matched),

        # Combined Trends + WARN
        ("trends 4 + warn +9",                 FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [9]}), eval_full),
        ("trends 4 + warn -8 (matched)",       FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [-8]}), eval_matched),
        ("trends 4 + warn +9 + warn -8 (m)",   FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [9, -8]}), eval_matched),

        # Variations of the joint set
        ("trends 3,4 + warn +9",               FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [3, 4], "warn_lags": [9]}), eval_full),
        ("trends 4 + warn +4,+9",              FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [4, 9]}), eval_full),
        ("trends 4 + warn +9 + warn +12",      FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [9, 12]}), eval_full),

        # Forward-leaning WARN combos (matched)
        ("warn -8 + warn -6 + warn -1 (m)",    FeatureSpec(**{**floor.__dict__, "warn_lags": [-8, -6, -1]}), eval_matched),
        ("trends 4 + warn -8,-6,-1 (m)",       FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [-8, -6, -1]}), eval_matched),
    ]

    print("=" * 100)
    print("Iteration 4: Combined LGBM + Trends + WARN")
    print("=" * 100)

    rows = []
    for label, spec, es in variants:
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, es, model="lgbm", refit_every=4)
        rows.append({
            "variant": label,
            "n_features": len(feats),
            "n_obs": r["n"],
            "mae": r["model_mae"],
            "rmse": r["model_rmse"],
            "bias": r["model_bias"],
            "eval_end": r["eval_end"],
        })
        print(fmt_summary(r, label))

    df = pd.DataFrame(rows).sort_values("mae")
    print()
    print("Leaderboard (best first):")
    print(df.to_string(index=False))

    # Highlight matched-sample comparisons
    matched = df[df["eval_end"] == "2026-03-21"].sort_values("mae")
    full = df[df["eval_end"] == "2026-05-09"].sort_values("mae")
    print()
    print("Full sample (n=97) leaderboard:")
    print(full.to_string(index=False))
    print()
    print("Matched sample (n=90) leaderboard:")
    print(matched.to_string(index=False))


if __name__ == "__main__":
    main()
