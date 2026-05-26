"""Iteration 2: LGBM + Google Trends with lag sweep. For each lag k>=0, include
all 11 trends series at lag k (alongside the iter-1 floor: lags 1..8 + seasonal).

Sweep approach: rather than sweep one global lag, we test progressively richer
lag bundles to see whether one historical alignment dominates or whether a fan
of lags is best.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run02_trends.py
"""

from __future__ import annotations

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]
    print(f"Trends signals available: {len(trends_cols)}")
    for c in trends_cols:
        print(f"  - {c}")
    print()

    # Eval window — Trends starts 2021-05; we keep train_start = 2022-01-01 so
    # every training row has lag<=12 trend features available. Eval still
    # 2024-07-01 onward to remain comparable to Phase-2.
    eval_spec = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")

    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    variants = [
        ("floor (target only)", floor),
        ("trends lag 0",        FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [0]})),
        ("trends lag 1",        FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [1]})),
        ("trends lag 2",        FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [2]})),
        ("trends lag 3",        FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [3]})),
        ("trends lag 4",        FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4]})),
        ("trends lags 0..1",    FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [0, 1]})),
        ("trends lags 0..2",    FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [0, 1, 2]})),
        ("trends lags 0..4",    FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [0, 1, 2, 3, 4]})),
    ]

    print("=" * 96)
    print("Iteration 2: LGBM + Google Trends, lag sweep")
    print(f"Train: {eval_spec.train_start}+   Eval: {eval_spec.eval_start}+ (matches phase-2)")
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
