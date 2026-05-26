"""Iteration 11: add holiday-in-target-week features + finer HPO.

Two-stage:
  (a) Test holiday feature in isolation: floor, ADP+Trends, full kitchen sink,
      with and without holidays, default LGBM. Confirms the lift exists.
  (b) Finer HPO grid around the iter-9 winner (n_est, lr, leaves, mcs) with
      holidays in the feature set. Includes finer-spaced lr (0.03, 0.04, 0.05),
      lower leaves (8, 10, 12, 15), and lower mcs (5, 10, 15) since the iter-9
      winner sat at the boundary of the original grid (leaves=10, mcs=10).

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run11_holidays_hpo.py
"""

from __future__ import annotations

import itertools
import pathlib

import numpy as np
import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"

EVAL_SPEC = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    # --- Stage (a): holiday feature isolated tests ---
    print("=" * 104)
    print("Iteration 11a: holiday-feature isolation test  (default LGBM)")
    print("=" * 104)
    variants = [
        ("floor",                        FeatureSpec(**{**floor.__dict__})),
        ("floor + holidays",             FeatureSpec(**{**floor.__dict__, "holidays": True})),
        ("adp+tr4",                      FeatureSpec(**{**floor.__dict__,
                                                          "trends_cols": trends_cols, "trends_lags": [4],
                                                          "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
        ("adp+tr4 + holidays",           FeatureSpec(**{**floor.__dict__,
                                                          "trends_cols": trends_cols, "trends_lags": [4],
                                                          "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8],
                                                          "holidays": True})),
        ("kitchen sink",                 FeatureSpec(**{**floor.__dict__,
                                                          "trends_cols": trends_cols, "trends_lags": [4],
                                                          "warn_lags": [9],
                                                          "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
        ("kitchen sink + holidays",      FeatureSpec(**{**floor.__dict__,
                                                          "trends_cols": trends_cols, "trends_lags": [4],
                                                          "warn_lags": [9],
                                                          "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8],
                                                          "holidays": True})),
    ]
    rows = []
    for label, spec in variants:
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, EVAL_SPEC, model="lgbm", refit_every=4)
        rows.append({"variant": label, "n_features": len(feats),
                      "mae": r["model_mae"], "rmse": r["model_rmse"], "bias": r["model_bias"]})
        print(fmt_summary(r, label))
    pd.DataFrame(rows).sort_values("mae").to_csv(HERE / "runs" / "iter11a_holiday_isolation.csv", index=False)

    # --- Stage (b): finer HPO around the iter-9 winner, with holidays included ---
    fine_grid = list(itertools.product(
        [1000, 1500, 2000, 3000],   # n_estimators
        [0.03, 0.04, 0.05],         # learning_rate
        [8, 10, 12, 15],            # num_leaves
        [5, 10, 15],                # min_child_samples
    ))
    print()
    print("=" * 104)
    print(f"Iteration 11b: finer LGBM HPO on adp+tr4+holidays  (grid size = {len(fine_grid)})")
    print("=" * 104)
    spec_best = FeatureSpec(**{**floor.__dict__,
                                "trends_cols": trends_cols, "trends_lags": [4],
                                "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8],
                                "holidays": True})
    panel, feats = build_panel(data, spec_best)
    print(f"  Feature count: {len(feats)}")
    best = None
    grid_rows = []
    for n_est, lr, leaves, mcs in fine_grid:
        params = {"n_estimators": n_est, "learning_rate": lr,
                   "num_leaves": leaves, "min_child_samples": mcs}
        r = walk_forward_eval(panel, feats, EVAL_SPEC, model="lgbm",
                               lgbm_params=params, refit_every=4)
        row = {"n_est": n_est, "lr": lr, "leaves": leaves, "mcs": mcs,
                "mae": r["model_mae"], "rmse": r["model_rmse"], "bias": r["model_bias"]}
        grid_rows.append(row)
        if best is None or r["model_mae"] < best["mae"]:
            best = {**row, "preds": r["preds"]}
    grid_df = pd.DataFrame(grid_rows).sort_values("mae")
    grid_df.to_csv(HERE / "runs" / "iter11b_finer_hpo.csv", index=False)

    print()
    print("Top 10 configs from finer HPO grid:")
    print(grid_df.head(10).to_string(index=False))
    print()
    print(f"Best: n_est={best['n_est']} lr={best['lr']} leaves={best['leaves']} mcs={best['mcs']}")
    print(f"      MAE={best['mae']:,.0f}  RMSE={best['rmse']:,.0f}  bias={best['bias']:+,.0f}")

    # --- Stage (c): re-ensemble with TimesFM ---
    bl = pd.read_parquet(DATA / "baselines.parquet")
    bl["origin"] = pd.to_datetime(bl["origin"])
    bl = bl[["origin", "y_true", "snaive", "tf25"]]
    preds = best["preds"][["origin", "y_pred"]].rename(columns={"y_pred": "lgbm"})
    preds["origin"] = pd.to_datetime(preds["origin"])
    df = bl.merge(preds, on="origin")
    weights = [round(w, 2) for w in np.arange(0.0, 1.0001, 0.05)]
    best_w, best_blend = 1.0, np.inf
    for w in weights:
        mae = (w * df["lgbm"] + (1 - w) * df["tf25"] - df["y_true"]).abs().mean()
        if mae < best_blend:
            best_w, best_blend = w, mae

    print()
    print("=" * 104)
    print("Iteration 11c: re-ensemble with TimesFM")
    print("=" * 104)
    lgbm_only = (df["lgbm"] - df["y_true"]).abs().mean()
    print(f"  LGBM alone (HPO+holidays): MAE={lgbm_only:,.0f}")
    print(f"  TimesFM 2.5 alone:         MAE={(df['tf25'] - df['y_true']).abs().mean():,.0f}")
    print(f"  Best blend w*LGBM + (1-w)*TF: w={best_w:.2f}, MAE={best_blend:,.0f}")
    print()
    print(f"Total improvement vs prev best blend (6,305): {6305 - best_blend:+,.0f}")
    print(f"Gap to 5,000 target: {best_blend - 5000:,.0f}")


if __name__ == "__main__":
    main()
