"""Iteration 5: hyperparameter sweep + XGBoost variant + residualize-on-snaive
variant on the strongest feature sets from iters 2-4.

We pin to the FULL sample (n=97, eval_end = 2026-05-09) so cross-variant MAEs
are apples-to-apples.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run05_hpo_xgb.py
"""

from __future__ import annotations

import itertools

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]
    eval_spec = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")

    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)
    spec_floor = floor
    spec_tr4 = FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4]})
    spec_w9 = FeatureSpec(**{**floor.__dict__, "warn_lags": [9]})
    spec_tr4_w9 = FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4], "warn_lags": [9]})

    feature_sets = {
        "floor": spec_floor,
        "tr4": spec_tr4,
        "w9": spec_w9,
        "tr4+w9": spec_tr4_w9,
    }

    # --- LGBM hyperparameter sweep on each feature set ---
    lgbm_grid = list(itertools.product(
        [400, 800, 1500],          # n_estimators
        [0.02, 0.04],               # learning_rate
        [15, 31, 63],               # num_leaves
        [10, 20, 40],               # min_child_samples
    ))

    print("=" * 104)
    print("Iteration 5a: LGBM HPO grid over 4 best feature sets  (n_grid_per_set = {})".format(len(lgbm_grid)))
    print("=" * 104)

    rows = []
    for fset_name, spec in feature_sets.items():
        panel, feats = build_panel(data, spec)
        best = None
        for n_est, lr, leaves, mcs in lgbm_grid:
            params = {"n_estimators": n_est, "learning_rate": lr,
                      "num_leaves": leaves, "min_child_samples": mcs}
            r = walk_forward_eval(panel, feats, eval_spec, model="lgbm",
                                   lgbm_params=params, refit_every=4)
            row = {
                "fset": fset_name,
                "model": "lgbm",
                "target": "level",
                "n_est": n_est, "lr": lr, "leaves": leaves, "mcs": mcs,
                "mae": r["model_mae"], "rmse": r["model_rmse"], "bias": r["model_bias"],
            }
            rows.append(row)
            if best is None or r["model_mae"] < best["mae"]:
                best = row
        print(f"  {fset_name:>8}  best lgbm  MAE={best['mae']:>7,.0f}  "
              f"params: n_est={best['n_est']} lr={best['lr']} leaves={best['leaves']} mcs={best['mcs']}")

    # --- XGBoost (smaller grid since it's slower; targeted around LGBM winners) ---
    xgb_grid = list(itertools.product(
        [400, 800, 1500],  # n_estimators
        [0.02, 0.04],       # lr
        [4, 6, 8],          # max_depth
        [5, 20],            # min_child_weight
    ))

    print()
    print("=" * 104)
    print(f"Iteration 5b: XGBoost HPO grid (n_grid_per_set = {len(xgb_grid)})")
    print("=" * 104)

    for fset_name, spec in feature_sets.items():
        panel, feats = build_panel(data, spec)
        best = None
        for n_est, lr, depth, mcw in xgb_grid:
            params = {"n_estimators": n_est, "learning_rate": lr,
                      "max_depth": depth, "min_child_weight": mcw}
            r = walk_forward_eval(panel, feats, eval_spec, model="xgb",
                                   xgb_params=params, refit_every=4)
            row = {
                "fset": fset_name,
                "model": "xgb",
                "target": "level",
                "n_est": n_est, "lr": lr, "depth": depth, "mcw": mcw,
                "mae": r["model_mae"], "rmse": r["model_rmse"], "bias": r["model_bias"],
            }
            rows.append(row)
            if best is None or r["model_mae"] < best["mae"]:
                best = row
        print(f"  {fset_name:>8}  best xgb   MAE={best['mae']:>7,.0f}  "
              f"params: n_est={best['n_est']} lr={best['lr']} depth={best['depth']} mcw={best['mcw']}")

    # --- Residualize-on-snaive variant: train model on (y - snaive); predict snaive + residual ---
    print()
    print("=" * 104)
    print("Iteration 5c: residualize-on-snaive (default LGBM params) on each feature set")
    print("=" * 104)
    for fset_name, spec in feature_sets.items():
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, eval_spec, model="lgbm",
                               refit_every=4, target_mode="residual_snaive")
        row = {
            "fset": fset_name,
            "model": "lgbm",
            "target": "residual_snaive",
            "n_est": "default", "lr": "default", "leaves": "default", "mcs": "default",
            "mae": r["model_mae"], "rmse": r["model_rmse"], "bias": r["model_bias"],
        }
        rows.append(row)
        print(fmt_summary(r, f"resid {fset_name}"))

    df = pd.DataFrame(rows).sort_values("mae")
    df.to_csv("forecasts/claims/initial_claims/lgbm_test/runs/iter5_results.csv", index=False)
    print()
    print("Top 20 overall (across LGBM HPO + XGB HPO + residual variants):")
    print(df.head(20).to_string(index=False))


if __name__ == "__main__":
    main()
