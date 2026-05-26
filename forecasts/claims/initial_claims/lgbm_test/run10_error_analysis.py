"""Iteration 10a: error analysis on the winning ADP+Trends LGBM and the blend.

For each origin, regenerate the predictions of the best LGBM (HPO winner),
join with TimesFM and snaive, and break the errors down by:
  * worst-25 weeks (largest |error|) — what is special about these?
  * calendar context (month, isocalendar week, holiday-week flag)
  * surprise on the target itself (week-over-week change in y)
  * agreement between LGBM and TimesFM (do they err in the same direction?)

Output is informational; the script writes runs/iter10_errors.csv with the
per-origin diagnostics for later inspection.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, load_data, walk_forward_eval

HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"
EVAL_SPEC = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")

# Winning HPO params from iter-9
BEST_LGBM_ADP_TR4 = {
    "n_estimators": 1500, "learning_rate": 0.04,
    "num_leaves": 10, "min_child_samples": 10,
}

# Approx US federal-holiday weeks that shift claims filing patterns
# (week_ending dates given as MM-DD for cross-year matching)
HOLIDAY_MMDD = {
    ("01", "01"): "new_years_week",  # week containing 1/1
    ("01", "15"): "mlk_week",
    ("02", "17"): "presidents_week",
    ("05", "26"): "memorial_week",
    ("07", "04"): "july4_week",
    ("09", "01"): "labor_day_week",
    ("10", "13"): "columbus_week",
    ("11", "11"): "veterans_week",
    ("11", "27"): "thanksgiving_week",
    ("12", "25"): "christmas_week",
}


def holiday_flag(d: pd.Timestamp) -> str:
    """Return holiday label if d's week (Sat ending) brackets a federal holiday."""
    # week covers d - 6d ... d (inclusive)
    start = d - pd.Timedelta(days=6)
    for offset in range(7):
        day = start + pd.Timedelta(days=offset)
        # Approx: match m/d match against fixed-day federal holidays.
        if (f"{day.month:02d}", f"{day.day:02d}") in {("01","01"),("07","04"),("11","11"),("12","25")}:
            return "fixed_holiday"
        # Memorial Day: last Mon of May. Labor Day: first Mon of Sep.
        # Thanksgiving: 4th Thu of Nov. MLK: 3rd Mon of Jan. Presidents': 3rd Mon of Feb. Columbus: 2nd Mon of Oct.
        if day.weekday() == 0:  # Monday
            if day.month == 5 and (day + pd.Timedelta(days=7)).month == 6:
                return "memorial"
            if day.month == 9 and day.day <= 7:
                return "labor_day"
            if day.month == 1 and 15 <= day.day <= 21:
                return "mlk"
            if day.month == 2 and 15 <= day.day <= 21:
                return "presidents"
            if day.month == 10 and 8 <= day.day <= 14:
                return "columbus"
        if day.weekday() == 3 and day.month == 11 and 22 <= day.day <= 28:
            return "thanksgiving"
    return ""


def main():
    data = load_data()
    trends_cols = [c for c in data["trends"].columns if c != "week_ending"]
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)
    spec_win = FeatureSpec(**{**floor.__dict__,
                                "trends_cols": trends_cols, "trends_lags": [4],
                                "adp_cols": ["adp_ner_us"], "adp_diff_lags": [8]})

    print("Regenerating winner predictions (ADP + Trends LGBM, HPO params) ...")
    panel, feats = build_panel(data, spec_win)
    r = walk_forward_eval(panel, feats, EVAL_SPEC, model="lgbm",
                            lgbm_params=BEST_LGBM_ADP_TR4, refit_every=4)
    lgbm = r["preds"].rename(columns={"y_pred": "lgbm"}).copy()
    lgbm["origin"] = pd.to_datetime(lgbm["origin"])

    # Join with TimesFM baseline
    bl = pd.read_parquet(DATA / "baselines.parquet")
    bl["origin"] = pd.to_datetime(bl["origin"])
    df = bl[["origin", "target_week", "y_true", "snaive", "tf25"]].merge(
        lgbm[["origin", "lgbm"]], on="origin")

    # Optimal blend from iter-9: 0.8 LGBM + 0.2 TF
    df["blend"] = 0.80 * df["lgbm"] + 0.20 * df["tf25"]

    # Errors
    for col in ("lgbm", "tf25", "blend", "snaive"):
        df[f"err_{col}"] = df[col] - df["y_true"]
        df[f"abs_err_{col}"] = df[f"err_{col}"].abs()

    # Holiday flag and calendar columns
    df["target_week"] = pd.to_datetime(df["target_week"])
    df["holiday"] = df["target_week"].apply(holiday_flag)
    df["target_month"] = df["target_week"].dt.month
    df["target_isoweek"] = df["target_week"].dt.isocalendar().week.astype(int)

    # Week-over-week surprise on the actual
    df = df.sort_values("origin").reset_index(drop=True)
    y_prev = df["y_true"].shift(1)
    df["y_surprise"] = df["y_true"] - y_prev  # how much did this week's actual change from last week's actual

    print()
    print("=" * 104)
    print("Headline error summary (matched 97 origins)")
    print("=" * 104)
    for col in ("lgbm", "tf25", "blend", "snaive"):
        mae = df[f"abs_err_{col}"].mean()
        rmse = np.sqrt((df[f"err_{col}"] ** 2).mean())
        bias = df[f"err_{col}"].mean()
        print(f"  {col:>8}  MAE={mae:>7,.0f}  RMSE={rmse:>7,.0f}  bias={bias:>+6,.0f}")

    print()
    print("=" * 104)
    print("Top 15 worst-error weeks for the BLEND")
    print("=" * 104)
    worst = df.sort_values("abs_err_blend", ascending=False).head(15)
    print(worst[["target_week", "y_true", "lgbm", "tf25", "blend", "err_blend",
                 "y_surprise", "holiday"]].to_string(index=False,
                 formatters={"y_true": "{:,.0f}".format, "lgbm": "{:,.0f}".format,
                              "tf25": "{:,.0f}".format, "blend": "{:,.0f}".format,
                              "err_blend": "{:+,.0f}".format,
                              "y_surprise": "{:+,.0f}".format}))

    print()
    print("=" * 104)
    print("Error by holiday flag")
    print("=" * 104)
    grp = df.groupby(df["holiday"].replace("", "(none)"))
    summary = grp.agg(n=("origin", "size"),
                       mae_blend=("abs_err_blend", "mean"),
                       mae_lgbm=("abs_err_lgbm", "mean"),
                       mae_tf=("abs_err_tf25", "mean"),
                       bias_blend=("err_blend", "mean"))
    print(summary.sort_values("mae_blend", ascending=False).to_string(
        formatters={"mae_blend": "{:,.0f}".format,
                     "mae_lgbm": "{:,.0f}".format,
                     "mae_tf": "{:,.0f}".format,
                     "bias_blend": "{:+,.0f}".format}))

    print()
    print("=" * 104)
    print("Error by target month")
    print("=" * 104)
    mgrp = df.groupby("target_month").agg(
        n=("origin", "size"),
        mae_blend=("abs_err_blend", "mean"),
        mae_lgbm=("abs_err_lgbm", "mean"),
        mae_tf=("abs_err_tf25", "mean"),
        bias_blend=("err_blend", "mean"),
    )
    print(mgrp.to_string(formatters={
        "mae_blend": "{:,.0f}".format, "mae_lgbm": "{:,.0f}".format,
        "mae_tf": "{:,.0f}".format, "bias_blend": "{:+,.0f}".format}))

    print()
    print("=" * 104)
    print("LGBM vs TimesFM error agreement (do they err in the same direction?)")
    print("=" * 104)
    same_sign = ((df["err_lgbm"] > 0) == (df["err_tf25"] > 0))
    print(f"  Same-sign errors: {same_sign.sum()} / {len(df)} ({same_sign.mean()*100:.0f}%)")
    print(f"  Corr(err_lgbm, err_tf25): {df['err_lgbm'].corr(df['err_tf25']):.3f}")
    print(f"  MAE on weeks where they agree on sign: {df.loc[same_sign, 'abs_err_blend'].mean():,.0f}")
    print(f"  MAE on weeks where they DIS-agree on sign: {df.loc[~same_sign, 'abs_err_blend'].mean():,.0f}")

    print()
    print("=" * 104)
    print("Error vs y_surprise (does the model miss harder on weeks with big WoW shifts?)")
    print("=" * 104)
    # Bucket by |y_surprise|
    df["surprise_bucket"] = pd.cut(df["y_surprise"].abs(),
                                     bins=[0, 5000, 10000, 20000, 50000, 1e9],
                                     labels=["0-5k", "5-10k", "10-20k", "20-50k", ">50k"])
    sg = df.groupby("surprise_bucket", observed=True).agg(
        n=("origin", "size"),
        mae_blend=("abs_err_blend", "mean"),
    )
    print(sg.to_string(formatters={"mae_blend": "{:,.0f}".format}))

    # Save the full diagnostics
    df.to_csv(HERE / "runs" / "iter10_errors.csv", index=False)
    print()
    print(f"Per-origin diagnostics saved to runs/iter10_errors.csv ({len(df)} rows)")


if __name__ == "__main__":
    main()
