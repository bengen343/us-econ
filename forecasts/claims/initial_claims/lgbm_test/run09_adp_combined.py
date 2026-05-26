"""Iteration 9: HPO the ADP winner, combine ADP+Trends+WARN, re-ensemble with TimesFM.

ADP NSA diff lag 8 was 6,616 with default LGBM — close to Trends-4 HPO (6,484).
Hypotheses to test:
  (a) HPO drops ADP variants by ~200 MAE same as it did for Trends-4.
  (b) ADP + Trends together may add (different signals: ADP = employment level
       change; Trends = search-side intent), unlike Trends+WARN which were
       partially redundant.
  (c) ADP + TimesFM ensemble may have weaker correlation than LGBM-tr4+TimesFM
       since ADP brings information TimesFM has no access to.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run09_adp_combined.py
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

LGBM_GRID = list(itertools.product(
    [400, 800, 1500],
    [0.02, 0.04],
    [10, 15, 31],
    [10, 20, 40],
))

def hpo_lgbm(panel, feats, label):
    best = None
    for n_est, lr, leaves, mcs in LGBM_GRID:
        params = {"n_estimators": n_est, "learning_rate": lr,
                   "num_leaves": leaves, "min_child_samples": mcs}
        r = walk_forward_eval(panel, feats, EVAL_SPEC, model="lgbm",
                               lgbm_params=params, refit_every=4)
        if best is None or r["model_mae"] < best["mae"]:
            best = {"label": label, "mae": r["model_mae"], "rmse": r["model_rmse"],
                    "bias": r["model_bias"], "params": params, "preds": r["preds"]}
    return best


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    # ---- HPO over several ADP feature sets ----
    candidates = [
        ("adp ner_us diff 8",          FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
        ("adp ner_sa_us level 10",     FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_sa_us"], "adp_lags": [10]})),
        ("adp ner_sa_us level 8",      FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_sa_us"], "adp_lags": [8]})),
        ("adp ner_us level 8",         FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_us"], "adp_lags": [8]})),
        ("adp ner_us diff 8 + level 10",
            FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_us", "adp_ner_sa_us"],
                            "adp_diff_lags": [8], "adp_lags": [10]})),
        # Kitchen sink: ADP + Trends + WARN
        ("kitchen sink (adp+tr4+w9)",
            FeatureSpec(**{**floor.__dict__,
                            "trends_cols": trends_cols, "trends_lags": [4],
                            "warn_lags": [9],
                            "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
        ("adp+tr4 (no warn)",
            FeatureSpec(**{**floor.__dict__,
                            "trends_cols": trends_cols, "trends_lags": [4],
                            "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
        ("adp+w9 (no trends)",
            FeatureSpec(**{**floor.__dict__,
                            "warn_lags": [9],
                            "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})),
    ]

    print("=" * 104)
    print(f"Iteration 9a: LGBM HPO over ADP variants and combined sets (grid size = {len(LGBM_GRID)})")
    print("=" * 104)

    results = {}
    for label, spec in candidates:
        panel, feats = build_panel(data, spec)
        best = hpo_lgbm(panel, feats, label)
        results[label] = best
        print(f"  {label:>35}  MAE={best['mae']:>7,.0f}  bias={best['bias']:>+5,.0f}  "
              f"params: {best['params']}  n_feats={len(feats)}")

    # ---- Also recompute the previously-winning specs for matched comparison ----
    floor_spec = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)
    spec_tr4 = FeatureSpec(**{**floor_spec.__dict__, "trends_cols": trends_cols, "trends_lags": [4]})

    panel, feats = build_panel(data, floor_spec)
    best_floor = hpo_lgbm(panel, feats, "floor (target only)")
    print(f"  {'floor (target only)':>35}  MAE={best_floor['mae']:>7,.0f}  params: {best_floor['params']}")

    panel, feats = build_panel(data, spec_tr4)
    best_tr4 = hpo_lgbm(panel, feats, "trends 4 (HPO)")
    print(f"  {'trends 4 (HPO)':>35}  MAE={best_tr4['mae']:>7,.0f}  params: {best_tr4['params']}")
    results["floor (target only)"] = best_floor
    results["trends 4 (HPO)"] = best_tr4

    # ---- TimesFM ensemble ----
    bl = pd.read_parquet(DATA / "baselines.parquet")
    bl["origin"] = pd.to_datetime(bl["origin"])
    bl = bl[["origin", "y_true", "snaive", "tf25"]]

    print()
    print("=" * 104)
    print("Iteration 9b: ensemble best LGBM variants with TimesFM 2.5")
    print("=" * 104)
    weights = [round(w, 2) for w in np.arange(0.0, 1.0001, 0.05)]
    ensemble_rows = []
    for label, best in results.items():
        preds = best["preds"][["origin", "y_pred"]].rename(columns={"y_pred": "lgbm"})
        preds["origin"] = pd.to_datetime(preds["origin"])
        merged = bl.merge(preds, on="origin")
        if len(merged) == 0:
            continue
        # Find optimal blend weight w*lgbm + (1-w)*tf25
        best_w, best_mae = 1.0, np.inf
        for w in weights:
            mae = (w * merged["lgbm"] + (1 - w) * merged["tf25"] - merged["y_true"]).abs().mean()
            if mae < best_mae:
                best_w, best_mae = w, mae
        lgbm_only_mae = (merged["lgbm"] - merged["y_true"]).abs().mean()
        ensemble_rows.append({
            "feature_set": label,
            "lgbm_alone_mae": lgbm_only_mae,
            "best_blend_w": best_w,
            "blend_mae": best_mae,
            "blend_lift": lgbm_only_mae - best_mae,
        })
        print(f"  {label:>35}  lgbm_alone={lgbm_only_mae:>7,.0f}  best_w={best_w:.2f}  blend={best_mae:>7,.0f}  lift={lgbm_only_mae - best_mae:>5,.0f}")

    df = pd.DataFrame(ensemble_rows).sort_values("blend_mae")
    df.to_csv(HERE / "runs" / "iter9_ensemble.csv", index=False)
    print()
    print("Final leaderboard (lowest blend MAE first):")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
