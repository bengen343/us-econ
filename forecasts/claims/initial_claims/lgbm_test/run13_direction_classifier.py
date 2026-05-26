"""Iteration 13: dedicated LGBM binary classifier for direction prediction.

Three layers:
  (1) HPO sweep over feature sets — find best raw hit rate.
  (2) Threshold-based emit decision: only emit when |P(up) - 0.5| > tau.
      For each tau, report (emit rate, hit rate among emissions).
  (3) Calibration: reliability table (binned predicted P(up) vs empirical
      up-rate), Brier score, ECE. Apply walk-forward isotonic recalibration
      and compare.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run13_direction_classifier.py
"""

from __future__ import annotations

import itertools
import pathlib

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from harness import EvalSpec, FeatureSpec, build_panel, load_data, walk_forward_classify

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
RUNS = HERE / "runs"

EVAL_SPEC = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]

    # Attach sa_input to the panel index ahead of build_panel by tweaking the
    # FeatureSpec — we'll use sa_lag0 (which is sa_input at origin) as the
    # "this_week" reference inside walk_forward_classify. To include lag-0, we
    # add 0 to target_lags.
    floor = FeatureSpec(target_lags=[0] + list(range(1, 9)), seasonal=True)
    spec_floor = floor
    spec_adp = FeatureSpec(**{**floor.__dict__,
                                "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})
    spec_tr4 = FeatureSpec(**{**floor.__dict__,
                                "trends_cols": trends_cols, "trends_lags": [4]})
    spec_adp_tr4 = FeatureSpec(**{**floor.__dict__,
                                    "trends_cols": trends_cols, "trends_lags": [4],
                                    "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})
    spec_adp_tr4_hol = FeatureSpec(**{**floor.__dict__,
                                        "trends_cols": trends_cols, "trends_lags": [4],
                                        "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8],
                                        "holidays": True})

    feature_sets = [
        ("floor",          spec_floor),
        ("adp",            spec_adp),
        ("tr4",            spec_tr4),
        ("adp+tr4",        spec_adp_tr4),
        ("adp+tr4+holidays", spec_adp_tr4_hol),
    ]

    grid = list(itertools.product(
        [400, 800, 1500, 3000],   # n_estimators
        [0.02, 0.04, 0.06],       # learning_rate
        [8, 15, 31],              # num_leaves
        [5, 10, 20],              # min_child_samples
    ))

    # ---- Layer 1: HPO across feature sets ----
    print("=" * 100)
    print(f"Iteration 13a: LGBM binary classifier — HPO grid size = {len(grid)} per feature set")
    print("=" * 100)

    best_per_set = {}
    for fset_name, spec in feature_sets:
        panel, feats = build_panel(data, spec)
        best = None
        for n_est, lr, leaves, mcs in grid:
            params = {"n_estimators": n_est, "learning_rate": lr,
                       "num_leaves": leaves, "min_child_samples": mcs}
            r = walk_forward_classify(panel, feats, EVAL_SPEC,
                                         lgbm_params=params, refit_every=4)
            if best is None or r["hit_rate"] > best["hit_rate"]:
                best = {**r, "params": params, "n_feats": len(feats), "fset": fset_name}
        best_per_set[fset_name] = best
        print(f"  {fset_name:>20}  hit_rate={best['hit_rate']:.2%}  brier={best['brier']:.4f}  "
              f"ece={best['ece']:.3f}  params: {best['params']}  n_feats={best['n_feats']}")

    # Pick the overall winner
    winner_name = max(best_per_set, key=lambda k: best_per_set[k]["hit_rate"])
    winner = best_per_set[winner_name]
    print()
    print(f"OVERALL BEST classifier: '{winner_name}' (hit rate {winner['hit_rate']:.2%})")

    pred = winner["preds"].copy()
    pred["origin"] = pd.to_datetime(pred["origin"])

    # ---- Layer 2: threshold-based emit decision ----
    print()
    print("=" * 100)
    print("Iteration 13b: threshold-based emit decision  (only emit when |P(up)-0.5| > tau)")
    print("=" * 100)
    print(f"{'tau':>5}  {'thresholds':>20}  {'n_emit':>7}  {'emit %':>7}  {'hit_rate_emitted':>17}  {'hit_rate_overall*':>17}")
    print("  * 'overall' = hit rate if you ALWAYS commit (no abstaining)")
    rows = []
    for tau in [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        emit_mask = (pred["p_up"] - 0.5).abs() >= tau
        n_emit = int(emit_mask.sum())
        if n_emit == 0:
            continue
        hr_emit = float(pred.loc[emit_mask, "hit"].mean())
        hr_all = float(pred["hit"].mean())
        thr_lo = 0.5 - tau
        thr_hi = 0.5 + tau
        rows.append({"tau": tau, "n_emit": n_emit, "emit_pct": n_emit / len(pred),
                     "hit_rate_emitted": hr_emit, "hit_rate_overall": hr_all})
        print(f"  {tau:>3.2f}  [<{thr_lo:.2f}, >{thr_hi:.2f}]   {n_emit:>3d}/{len(pred)}    "
              f"{n_emit / len(pred):>5.1%}   {hr_emit:>14.2%}   {hr_all:>14.2%}")

    pd.DataFrame(rows).to_csv(RUNS / "iter13b_thresholds.csv", index=False)

    # ---- Layer 3: calibration ----
    print()
    print("=" * 100)
    print("Iteration 13c: calibration — reliability table (raw probabilities)")
    print("=" * 100)
    rel = winner["reliability"].copy()
    rel = rel.assign(gap=(rel["avg_p"] - rel["emp_rate"]))
    print(rel.to_string(formatters={"avg_p": "{:.2f}".format,
                                       "emp_rate": "{:.2f}".format,
                                       "gap": "{:+.2f}".format}))
    print(f"\n  Brier score (raw): {winner['brier']:.4f}    ECE (raw): {winner['ece']:.3f}")

    # Walk-forward isotonic recalibration: at each origin t in eval, fit isotonic
    # on the prior eval origins' (p_up, y_dir) pairs and apply.
    pred = pred.sort_values("origin").reset_index(drop=True)
    p_iso = []
    for i in range(len(pred)):
        if i < 12:
            # Not enough calibration data; pass through.
            p_iso.append(pred.loc[i, "p_up"])
            continue
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso.fit(pred.loc[:i - 1, "p_up"].values, pred.loc[:i - 1, "y_dir"].values)
        p_iso.append(float(iso.predict([pred.loc[i, "p_up"]])[0]))
    pred["p_up_iso"] = p_iso
    pred["pred_dir_iso"] = (pred["p_up_iso"] >= 0.5).astype(int)
    pred["hit_iso"] = (pred["pred_dir_iso"] == pred["y_dir"]).astype(int)
    iso_brier = float(((pred["p_up_iso"] - pred["y_dir"]) ** 2).mean())

    # Recompute reliability after isotonic
    bins = pd.cut(pred["p_up_iso"], bins=[0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0001], include_lowest=True)
    rel_iso = pred.groupby(bins, observed=True).agg(n=("p_up_iso", "size"),
                                                       avg_p=("p_up_iso", "mean"),
                                                       emp_rate=("y_dir", "mean"))
    ece_iso = float((rel_iso["n"] * (rel_iso["avg_p"] - rel_iso["emp_rate"]).abs()).sum()
                     / max(rel_iso["n"].sum(), 1))
    print()
    print("After walk-forward isotonic recalibration:")
    print(rel_iso.to_string(formatters={"avg_p": "{:.2f}".format, "emp_rate": "{:.2f}".format}))
    print(f"\n  Brier score (iso): {iso_brier:.4f}    ECE (iso): {ece_iso:.3f}")
    print(f"  Hit rate (iso):    {pred['hit_iso'].mean():.2%}    Hit rate (raw): {pred['hit'].mean():.2%}")

    pred.to_csv(RUNS / "iter13_predictions.csv", index=False)

    # ---- Combined operational table: threshold sweep using calibrated probs ----
    print()
    print("=" * 100)
    print("Iteration 13d: combined — threshold sweep with CALIBRATED probabilities")
    print("=" * 100)
    print(f"{'tau':>5}  {'n_emit':>7}  {'emit %':>7}  {'hit_rate_emitted':>17}")
    for tau in [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        emit_mask = (pred["p_up_iso"] - 0.5).abs() >= tau
        n_emit = int(emit_mask.sum())
        if n_emit == 0:
            continue
        hr_emit = float(pred.loc[emit_mask, "hit_iso"].mean())
        print(f"  {tau:>3.2f}   {n_emit:>3d}/{len(pred)}    {n_emit / len(pred):>5.1%}   {hr_emit:>14.2%}")


if __name__ == "__main__":
    main()
