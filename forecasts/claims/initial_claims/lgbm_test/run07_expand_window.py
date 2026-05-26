"""Iteration 7: expand training window with optional COVID mask.

Limitation: Trends data starts 2021-05-29, so any model that uses Trends
features is intrinsically bound to ~2022+ training. To get the benefit of
a longer training window, we need to either:
  (a) Use target-only LGBM (no Trends) but with a long pre-COVID/post-COVID window
  (b) Use a longer window AND drop the COVID block from training

We test both. If the longer-window floor models match or beat the tr4
2022+ winner, then we have an alternative path that doesn't need Trends.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run07_expand_window.py
"""

from __future__ import annotations

import itertools

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)
    floor_w9 = FeatureSpec(**{**floor.__dict__, "warn_lags": [9]})

    eval_configs = [
        ("2022+ no mask     ", EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")),
        ("2018+ no mask     ", EvalSpec(train_start="2018-01-01", eval_start="2024-07-01")),
        ("2018+ COVID mask  ", EvalSpec(train_start="2018-01-01", eval_start="2024-07-01",
                                         train_mask_ranges=(("2020-03-01", "2021-06-30"),))),
        ("2015+ COVID mask  ", EvalSpec(train_start="2015-01-01", eval_start="2024-07-01",
                                         train_mask_ranges=(("2020-03-01", "2021-06-30"),))),
        ("2010+ COVID mask  ", EvalSpec(train_start="2010-01-01", eval_start="2024-07-01",
                                         train_mask_ranges=(("2020-03-01", "2021-06-30"),))),
    ]

    print("=" * 100)
    print("Iteration 7a: floor (target-only) LGBM across training windows")
    print("=" * 100)

    for label, ec in eval_configs:
        panel, feats = build_panel(data, floor)
        # default LGBM
        r = walk_forward_eval(panel, feats, ec, model="lgbm", refit_every=4)
        # tuned LGBM (best floor params from iter-5)
        r2 = walk_forward_eval(panel, feats, ec, model="lgbm",
                                lgbm_params={"n_estimators": 1500, "learning_rate": 0.02,
                                              "num_leaves": 15, "min_child_samples": 20},
                                refit_every=4)
        print(f"  [{label}]  default MAE={r['model_mae']:>7,.0f}   tuned MAE={r2['model_mae']:>7,.0f}")

    print()
    print("=" * 100)
    print("Iteration 7b: WARN-+9 LGBM across training windows (WARN goes back to 2009)")
    print("=" * 100)

    for label, ec in eval_configs:
        panel, feats = build_panel(data, floor_w9)
        r = walk_forward_eval(panel, feats, ec, model="lgbm", refit_every=4)
        r2 = walk_forward_eval(panel, feats, ec, model="lgbm",
                                lgbm_params={"n_estimators": 400, "learning_rate": 0.02,
                                              "num_leaves": 15, "min_child_samples": 10},
                                refit_every=4)
        print(f"  [{label}]  default MAE={r['model_mae']:>7,.0f}   tuned MAE={r2['model_mae']:>7,.0f}")

    print()
    print("=" * 100)
    print("Iteration 7c: HPO mini-sweep on best window for WARN-+9 (since it has long history)")
    print("=" * 100)

    best_ec_label, best_ec = eval_configs[-1]  # the most-expansive setup
    print(f"Using eval config: {best_ec_label}")
    panel, feats = build_panel(data, floor_w9)

    grid = list(itertools.product(
        [400, 800, 1500, 3000],
        [0.01, 0.02, 0.04],
        [10, 15, 31],
        [10, 20, 40],
    ))
    best = None
    for n_est, lr, leaves, mcs in grid:
        params = {"n_estimators": n_est, "learning_rate": lr,
                   "num_leaves": leaves, "min_child_samples": mcs}
        r = walk_forward_eval(panel, feats, best_ec, model="lgbm",
                               lgbm_params=params, refit_every=4)
        if best is None or r["model_mae"] < best[-1]:
            best = (n_est, lr, leaves, mcs, r["model_mae"])
    print(f"  Best for w9 / {best_ec_label}: n_est={best[0]} lr={best[1]} leaves={best[2]} mcs={best[3]}  MAE={best[4]:,.0f}")

    # Same for floor (target-only) with extended window
    grid_floor = list(itertools.product(
        [400, 800, 1500, 3000],
        [0.01, 0.02, 0.04],
        [10, 15, 31, 63],
        [10, 20, 40],
    ))
    best = None
    panel, feats = build_panel(data, floor)
    for n_est, lr, leaves, mcs in grid_floor:
        params = {"n_estimators": n_est, "learning_rate": lr,
                   "num_leaves": leaves, "min_child_samples": mcs}
        r = walk_forward_eval(panel, feats, best_ec, model="lgbm",
                               lgbm_params=params, refit_every=4)
        if best is None or r["model_mae"] < best[-1]:
            best = (n_est, lr, leaves, mcs, r["model_mae"])
    print(f"  Best for floor / {best_ec_label}: n_est={best[0]} lr={best[1]} leaves={best[2]} mcs={best[3]}  MAE={best[4]:,.0f}")


if __name__ == "__main__":
    main()
