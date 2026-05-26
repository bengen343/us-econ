"""Iteration 12: direction-prediction baseline.

For each of the 97 eval origins:
  this_week     = sa_input value at week_ending = origin (latest observed)
  next_week     = sa_actual at week_ending = origin + 7d (target)
  true_dir      = sign(next_week - this_week)
  model_dir     = sign(model_pred - this_week)
  hit           = (model_dir == true_dir)

Reports hit rate for each model and several baselines:
  - always_up, always_down (constant)
  - persistence (predict same direction as last WoW change)
  - snaive_dir (same direction as same week last year)
  - tf25, lgbm (ADP+tr4 HPO), blend (0.8*lgbm + 0.2*tf25)
  - magnitude-conditioned: separate hit rate by |true_change|
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
RUNS = HERE / "runs"


def sgn(x):
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def main():
    # Load the iter-10 panel — has lgbm, tf25, blend, snaive, y_true per origin
    df = pd.read_csv(RUNS / "iter10_errors.csv")
    df["origin"] = pd.to_datetime(df["origin"])
    df["target_week"] = pd.to_datetime(df["target_week"])

    # Join sa_input at origin to get "this week" reference
    sa = pd.read_parquet(DATA / "sa_claims.parquet")
    sa["week_ending"] = pd.to_datetime(sa["week_ending"])
    sa = sa.set_index("week_ending")
    df["this_week"] = df["origin"].map(sa["sa_input"]).astype(float)

    # True WoW change and direction
    df["true_change"] = df["y_true"] - df["this_week"]
    df["true_dir"] = df["true_change"].apply(sgn)

    # Baselines
    df["pred_always_up"] = 1
    df["pred_always_down"] = -1
    df["pred_random"] = np.random.default_rng(42).choice([-1, 1], len(df))

    # Persistence: predict direction = sign(this_week - last_week)
    df["last_week"] = df["origin"].apply(lambda d: sa["sa_input"].get(d - pd.Timedelta(days=7), np.nan))
    df["pred_persistence"] = (df["this_week"] - df["last_week"]).apply(sgn)

    # Snaive direction: predict direction = sign(sa_input[T+1-364d] - sa_input[T-364d])
    def snaive_dir(d):
        prev_target = d + pd.Timedelta(days=7) - pd.Timedelta(days=364)
        prev_origin = d - pd.Timedelta(days=364)
        a = sa["sa_input"].get(prev_target, np.nan)
        b = sa["sa_input"].get(prev_origin, np.nan)
        if np.isnan(a) or np.isnan(b):
            return 0
        return sgn(a - b)
    df["pred_snaive_dir"] = df["origin"].apply(snaive_dir)

    # Model-derived direction predictions
    for col in ("tf25", "lgbm", "blend", "snaive"):
        df[f"pred_dir_{col}"] = (df[col] - df["this_week"]).apply(sgn)

    # Hit rate calculation
    print("=" * 88)
    print(f"Direction-prediction hit rate (n={len(df)}, eval 2024-07-06 .. 2026-05-09)")
    print("=" * 88)
    print(f"  True-direction distribution: up={int((df['true_dir']==1).sum())}, "
          f"down={int((df['true_dir']==-1).sum())}, flat={int((df['true_dir']==0).sum())}")
    print()

    methods = [
        ("always_up",          df["pred_always_up"]),
        ("always_down",        df["pred_always_down"]),
        ("random",             df["pred_random"]),
        ("persistence (WoW)",  df["pred_persistence"]),
        ("snaive_direction",   df["pred_snaive_dir"]),
        ("TimesFM 2.5",        df["pred_dir_tf25"]),
        ("LGBM (adp+tr4 HPO)", df["pred_dir_lgbm"]),
        ("Blend 0.8/0.2",      df["pred_dir_blend"]),
        ("snaive_h1_pred",     df["pred_dir_snaive"]),
    ]
    for label, pred in methods:
        hit = (pred == df["true_dir"]).mean()
        # Per-direction accuracy (precision-style)
        up_acc  = ((pred == 1)  & (df["true_dir"] == 1)).sum()  / max((df["true_dir"] == 1).sum(), 1)
        dn_acc  = ((pred == -1) & (df["true_dir"] == -1)).sum() / max((df["true_dir"] == -1).sum(), 1)
        print(f"  {label:>22}  hit_rate={hit:.2%}  recall_up={up_acc:.2%}  recall_down={dn_acc:.2%}")

    # Magnitude-conditioned hit rate for the best models
    print()
    print("=" * 88)
    print("Hit rate by |true_change| (does the model do better on big moves?)")
    print("=" * 88)
    df["abs_change"] = df["true_change"].abs()
    df["change_bucket"] = pd.cut(
        df["abs_change"],
        bins=[0, 3000, 7000, 15000, 30000, 1e9],
        labels=["<3k", "3-7k", "7-15k", "15-30k", ">30k"],
    )

    for label, pred_col in [("Blend", "pred_dir_blend"), ("LGBM", "pred_dir_lgbm"),
                              ("TimesFM", "pred_dir_tf25"), ("persistence", "pred_persistence")]:
        df[f"hit_{label}"] = (df[pred_col] == df["true_dir"]).astype(int)

    summary = df.groupby("change_bucket", observed=True).agg(
        n=("origin", "size"),
        Blend=("hit_Blend", "mean"),
        LGBM=("hit_LGBM", "mean"),
        TimesFM=("hit_TimesFM", "mean"),
        persistence=("hit_persistence", "mean"),
    )
    print(summary.to_string(formatters={c: "{:.0%}".format for c in ("Blend", "LGBM", "TimesFM", "persistence")}))

    # Confusion matrix for the best model
    print()
    print("=" * 88)
    print("Confusion matrix — Blend")
    print("=" * 88)
    cm = pd.crosstab(df["true_dir"], df["pred_dir_blend"],
                       rownames=["true_dir"], colnames=["pred_dir"], margins=True)
    print(cm.to_string())

    # Save for inspection
    df.to_csv(RUNS / "iter12_direction.csv", index=False)
    print()
    print(f"Saved per-origin direction analysis to runs/iter12_direction.csv")


if __name__ == "__main__":
    main()
