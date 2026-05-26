"""Iteration 1: target-lags-only LGBM. Establishes the floor that exog signals
must beat. Also reports a local snaive baseline.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run01_baseline.py
"""

from __future__ import annotations

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()

    print("=" * 88)
    print("Iteration 1: LGBM with target lags only (no exog signals)")
    print("Eval window mirrors Phase-2: origin in [2024-07-01, last_complete_target]")
    print("Published baselines: TimesFM 2.5 ~6,800 MAE  |  ens_w60 ~8,300 MAE")
    print("=" * 88)

    variants = [
        ("lags 1..8 + seasonal",     FeatureSpec(target_lags=list(range(1, 9)),  seasonal=True)),
        ("lags 1..13 + seasonal",    FeatureSpec(target_lags=list(range(1, 14)), seasonal=True)),
        ("lags 1..26 + seasonal",    FeatureSpec(target_lags=list(range(1, 27)), seasonal=True)),
        ("lags 1..8 no seasonal",    FeatureSpec(target_lags=list(range(1, 9)),  seasonal=False)),
        ("lags 1..8 + sl52 only",    FeatureSpec(target_lags=list(range(1, 9)) + [52], seasonal=False)),
    ]
    eval_spec = EvalSpec(train_start="2010-01-01", eval_start="2024-07-01")

    results = []
    for label, spec in variants:
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, eval_spec, model="lgbm", refit_every=4)
        results.append((label, r))
        print(fmt_summary(r, label))

    print()
    print("Best target-only MAE:",
          min(r[1]["model_mae"] for r in results),
          "(target to beat in iterations 2-5: this number; ultimate goal: <5,000)")


if __name__ == "__main__":
    main()
