"""Iteration 6b: ensemble best LGBM with TimesFM 2.5 over matched 97 origins.

Loads baselines.parquet (from run06_baseline_pull.py), regenerates the best
LGBM (Trends-lag-4 + HPO winners) predictions per origin, and searches the
weight w in y_blend = w*LGBM + (1-w)*TimesFM.

Also tries a 3-way blend with snaive (which would mirror the production
ens_w60 spirit) and an HPO-tuned w9-LGBM as a second leg.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, load_data, walk_forward_eval

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"

EVAL_SPEC = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")

# HPO winners from iter-5
BEST_LGBM_PARAMS_TR4 = {
    "n_estimators": 1500, "learning_rate": 0.04,
    "num_leaves": 15, "min_child_samples": 10,
}
BEST_LGBM_PARAMS_W9 = {
    "n_estimators": 400, "learning_rate": 0.02,
    "num_leaves": 15, "min_child_samples": 10,
}
BEST_LGBM_PARAMS_FLOOR = {
    "n_estimators": 1500, "learning_rate": 0.02,
    "num_leaves": 15, "min_child_samples": 20,
}


def _lgbm_preds(spec: FeatureSpec, params: dict, label: str) -> pd.DataFrame:
    data = load_data()
    panel, feats = build_panel(data, spec)
    r = walk_forward_eval(panel, feats, EVAL_SPEC, model="lgbm",
                          lgbm_params=params, refit_every=4)
    out = r["preds"].rename(columns={"y_pred": label}).drop(columns=["y_true", "snaive"])
    out["origin"] = pd.to_datetime(out["origin"])
    return out


def main():
    bl = pd.read_parquet(DATA / "baselines.parquet")
    bl["origin"] = pd.to_datetime(bl["origin"])
    bl = bl[["origin", "target_week", "y_true", "snaive", "tf25"]]

    print("Regenerating tuned LGBM predictions ...")
    trends_cols = [f"trends_{n}" for n in [
        "gasoline_topic","jobs_cat","q_file_for_unemployment","q_gas_prices",
        "q_jobs_hiring","q_jobs_near_me","q_layoffs","q_road_trip",
        "q_unemployment_benefits","q_unemployment_office","unemployment_topic",
    ]]
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)
    spec_tr4 = FeatureSpec(**{**floor.__dict__, "trends_cols": trends_cols, "trends_lags": [4]})
    spec_w9 = FeatureSpec(**{**floor.__dict__, "warn_lags": [9]})

    lgbm_tr4 = _lgbm_preds(spec_tr4, BEST_LGBM_PARAMS_TR4, "lgbm_tr4")
    lgbm_w9 = _lgbm_preds(spec_w9, BEST_LGBM_PARAMS_W9, "lgbm_w9")
    lgbm_floor = _lgbm_preds(floor, BEST_LGBM_PARAMS_FLOOR, "lgbm_floor")

    df = bl.merge(lgbm_tr4, on="origin").merge(lgbm_w9, on="origin").merge(lgbm_floor, on="origin")
    print(f"Merged panel: {len(df)} rows")

    # Per-model standalone MAEs
    print()
    print("=" * 88)
    print("Standalone MAE (matched 97 origins, 2024-07-06 .. 2026-05-09)")
    print("=" * 88)
    for col in ("tf25", "lgbm_floor", "lgbm_w9", "lgbm_tr4", "snaive"):
        mae = (df[col] - df["y_true"]).abs().mean()
        rmse = np.sqrt(((df[col] - df["y_true"]) ** 2).mean())
        bias = (df[col] - df["y_true"]).mean()
        print(f"  {col:>12}  MAE={mae:>7,.0f}  RMSE={rmse:>7,.0f}  bias={bias:>+7,.0f}")

    # Pairwise blend sweep: w * A + (1-w) * B
    print()
    print("=" * 88)
    print("Pairwise blend sweep: y_blend = w*A + (1-w)*B  (h=1 MAE)")
    print("=" * 88)
    weights = [round(w, 2) for w in np.arange(0.0, 1.0001, 0.05)]
    pairs = [
        ("lgbm_tr4", "tf25"),
        ("lgbm_w9",  "tf25"),
        ("lgbm_floor", "tf25"),
        ("lgbm_tr4", "snaive"),
    ]
    leaderboard = []
    for a, b in pairs:
        best = (None, None, np.inf)
        for w in weights:
            pred = w * df[a] + (1 - w) * df[b]
            mae = (pred - df["y_true"]).abs().mean()
            if mae < best[2]:
                best = (w, b, mae)
        # Show full sweep curve
        print(f"\n  {a} + {b}:")
        for w in weights[::2]:  # every other for compactness
            pred = w * df[a] + (1 - w) * df[b]
            mae = (pred - df["y_true"]).abs().mean()
            mark = " <-- best in coarse grid" if w == best[0] else ""
            print(f"    w={w:.2f}  MAE={mae:>7,.0f}{mark}")
        print(f"  best for {a} + {b}: w={best[0]:.2f}, MAE={best[2]:,.0f}")
        leaderboard.append({"blend": f"{a}+{b}", "w": best[0], "mae": best[2]})

    # Three-way blend: w1*tr4 + w2*tf25 + w3*snaive, simplex search
    print()
    print("=" * 88)
    print("Three-way blend (lgbm_tr4 + tf25 + snaive), simplex w1+w2+w3=1, step=0.05")
    print("=" * 88)
    best3 = (None, None, None, np.inf)
    grid_step = 0.05
    for w1 in np.arange(0.0, 1.0001, grid_step):
        for w2 in np.arange(0.0, 1.0001 - w1, grid_step):
            w3 = 1.0 - w1 - w2
            pred = w1 * df["lgbm_tr4"] + w2 * df["tf25"] + w3 * df["snaive"]
            mae = (pred - df["y_true"]).abs().mean()
            if mae < best3[3]:
                best3 = (round(w1, 3), round(w2, 3), round(w3, 3), mae)
    print(f"  best: w_lgbm_tr4={best3[0]:.2f}, w_tf25={best3[1]:.2f}, w_snaive={best3[2]:.2f}  MAE={best3[3]:,.0f}")

    # Three-way: lgbm_tr4 + lgbm_w9 + tf25
    best3b = (None, None, None, np.inf)
    for w1 in np.arange(0.0, 1.0001, grid_step):
        for w2 in np.arange(0.0, 1.0001 - w1, grid_step):
            w3 = 1.0 - w1 - w2
            pred = w1 * df["lgbm_tr4"] + w2 * df["lgbm_w9"] + w3 * df["tf25"]
            mae = (pred - df["y_true"]).abs().mean()
            if mae < best3b[3]:
                best3b = (round(w1, 3), round(w2, 3), round(w3, 3), mae)
    print(f"  best: w_tr4={best3b[0]:.2f}, w_w9={best3b[1]:.2f}, w_tf25={best3b[2]:.2f}  MAE={best3b[3]:,.0f}")
    leaderboard.append({"blend": "tr4+w9+tf25", "w": f"{best3b[0]},{best3b[1]},{best3b[2]}", "mae": best3b[3]})

    print()
    print("=" * 88)
    print("Final leaderboard")
    print("=" * 88)
    standalone = pd.DataFrame([
        {"model": "tf25", "mae": (df["tf25"] - df["y_true"]).abs().mean()},
        {"model": "lgbm_tr4 (HPO)", "mae": (df["lgbm_tr4"] - df["y_true"]).abs().mean()},
        {"model": "lgbm_w9 (HPO)", "mae": (df["lgbm_w9"] - df["y_true"]).abs().mean()},
        {"model": "lgbm_floor (HPO)", "mae": (df["lgbm_floor"] - df["y_true"]).abs().mean()},
        {"model": "snaive", "mae": (df["snaive"] - df["y_true"]).abs().mean()},
    ])
    standalone = standalone.sort_values("mae")
    print("Standalone:")
    print(standalone.to_string(index=False))
    print()
    print("Blends:")
    for r in sorted(leaderboard, key=lambda x: x["mae"]):
        print(f"  {r['blend']:>20}  w={r['w']}  MAE={r['mae']:,.0f}")

    # Save predictions panel
    df.to_csv(HERE / "runs" / "iter6_predictions.csv", index=False)


if __name__ == "__main__":
    main()
