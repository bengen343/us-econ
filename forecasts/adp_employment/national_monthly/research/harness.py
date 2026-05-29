r"""Walk-forward research harness for the ADP national-monthly headline nowcast.

Read-only. Pulls BigQuery, builds the monthly panel, runs an expanding-window
walk-forward backtest comparing naive baselines against small regularised
regressions over ablated feature groups, and prints an honest scorecard
(MAE / RMSE / median / % within 12.5k / direction hit-rate) plus a live
forecast for the next unreleased month.

Run:  .\.venv\Scripts\python.exe -m forecasts.adp_employment.national_monthly.research.harness
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from forecasts.adp_employment.national_monthly.research import data
from forecasts.adp_employment.national_monthly.research import panel as panel_mod

TRAIN_FLOOR = pd.Timestamp("2022-09-01")  # ADP-Stanford new-methodology era
MIN_TRAIN = 18                            # months before we trust a fit
GOAL = 12_500                             # user's stretch accuracy target
ALPHAS = (0.3, 1.0, 3.0, 10.0, 30.0, 100.0)


@dataclass
class Spec:
    name: str
    groups: tuple[str, ...]  # feature groups from panel.build_panel


SPECS = [
    Spec("momentum", ("momentum",)),
    Spec("claims", ("claims",)),
    Spec("trends", ("trends",)),
    Spec("mom+claims", ("momentum", "claims")),
    Spec("mom+trends", ("momentum", "trends")),
    Spec("claims+trends", ("claims", "trends")),
    Spec("mom+claims+trends", ("momentum", "claims", "trends")),
]


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def score(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    err = y_pred - y_true
    ae = np.abs(err)
    # Directional hit: sign of the headline itself (job gain vs loss).
    dir_hit = np.mean((y_pred > 0) == (y_true > 0))
    return {
        "n": len(err),
        "MAE": float(np.mean(ae)),
        "RMSE": float(np.sqrt(np.mean(err**2))),
        "MedAE": float(np.median(ae)),
        f"%<{GOAL//1000}k": float(np.mean(ae <= GOAL) * 100),
        "%<25k": float(np.mean(ae <= 25_000) * 100),
        "%<50k": float(np.mean(ae <= 50_000) * 100),
        "dir%": float(dir_hit * 100),
    }


# --------------------------------------------------------------------------- #
# Walk-forward predictors
# --------------------------------------------------------------------------- #
def walk_forward_baselines(panel: pd.DataFrame) -> pd.DataFrame:
    """Naive baselines requiring no fit, evaluated on the test window."""
    rows = []
    obs = panel.dropna(subset=["y"])
    for month in _test_months(panel):
        hist = obs.loc[obs.index < month, "y"]
        if len(hist) < MIN_TRAIN:
            continue
        y_true = panel.at[month, "y"]
        rows.append({
            "month": month,
            "y_true": y_true,
            "rw": hist.iloc[-1],                 # last headline
            "mean3": hist.iloc[-3:].mean(),      # trailing 3-month mean
            "longrun": hist.mean(),              # expanding mean
        })
    return pd.DataFrame(rows).set_index("month")


def walk_forward_model(panel: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Expanding-window RidgeCV over the given features."""
    rows = []
    sub = panel[["y", *feature_cols]].copy()
    for month in _test_months(panel):
        train = sub.loc[sub.index < month].dropna(subset=["y", *feature_cols])
        if len(train) < MIN_TRAIN:
            continue
        x_now = sub.loc[[month], feature_cols]
        if x_now.isna().any(axis=None):
            continue
        model = make_pipeline(
            StandardScaler(),
            RidgeCV(alphas=ALPHAS),
        )
        model.fit(train[feature_cols].values, train["y"].values)
        pred = float(model.predict(x_now.values)[0])
        rows.append({"month": month, "y_true": panel.at[month, "y"], "pred": pred})
    if not rows:
        return pd.DataFrame(columns=["y_true", "pred"]).rename_axis("month")
    return pd.DataFrame(rows).set_index("month")


def _test_months(panel: pd.DataFrame) -> list[pd.Timestamp]:
    """Months with an observed target, at/after the floor (excludes live row)."""
    obs = panel.dropna(subset=["y"])
    return [mth for mth in obs.index if mth >= TRAIN_FLOOR]


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _fmt(d: dict[str, float]) -> str:
    return (f"n={d['n']:>3.0f}  MAE={d['MAE']:>8,.0f}  RMSE={d['RMSE']:>8,.0f}  "
            f"Med={d['MedAE']:>8,.0f}  {('%<'+str(GOAL//1000)+'k'):>6}="
            f"{d[f'%<{GOAL//1000}k']:>5.1f}  %<25k={d['%<25k']:>5.1f}  "
            f"%<50k={d['%<50k']:>5.1f}  dir%={d['dir%']:>5.1f}")


def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    d = data.pull_all()
    panel, groups = panel_mod.build_panel(d["monthly"], d["pulse"], d["claims"],
                                          d["trends"])

    obs = panel.dropna(subset=["y"])
    print(f"\nPanel: {len(panel)} months ({panel.index.min().date()} .. "
          f"{panel.index.max().date()}), {len(obs)} with observed headline.")
    print(f"New-methodology test window: months >= {TRAIN_FLOOR.date()} "
          f"with >= {MIN_TRAIN} prior training months.\n")

    # ---- Baselines ---------------------------------------------------------
    base = walk_forward_baselines(panel)
    common_idx = base.index
    print("=" * 100)
    print("BASELINES  (test window = aligned origins below)")
    print("=" * 100)
    results: list[tuple[str, dict]] = []
    for col in ("rw", "mean3", "longrun"):
        s = score(base["y_true"].values, base[col].values)
        results.append((f"baseline:{col}", s))
        print(f"  {col:<22} {_fmt(s)}")

    # ---- Models (scored on the SAME aligned origins as baselines) ----------
    print("\n" + "=" * 100)
    print("RIDGE MODELS  (RidgeCV, expanding window, z-scored features)")
    print("=" * 100)
    for spec in SPECS:
        cols = [c for g in spec.groups for c in groups[g]]
        preds = walk_forward_model(panel, cols)
        aligned = preds.reindex(common_idx).dropna(subset=["pred", "y_true"])
        if aligned.empty:
            print(f"  {spec.name:<22} (no aligned origins)")
            continue
        s = score(aligned["y_true"].values, aligned["pred"].values)
        results.append((f"model:{spec.name}", s))
        print(f"  {spec.name:<22} {_fmt(s)}")

    # ---- Ranking -----------------------------------------------------------
    print("\n" + "=" * 100)
    print(f"RANKED BY MAE  (goal: MAE <= {GOAL:,})")
    print("=" * 100)
    for name, s in sorted(results, key=lambda kv: kv[1]["MAE"]):
        flag = "  <-- meets goal" if s["MAE"] <= GOAL else ""
        print(f"  {name:<28} MAE={s['MAE']:>8,.0f}  RMSE={s['RMSE']:>8,.0f}"
              f"  %<{GOAL//1000}k={s[f'%<{GOAL//1000}k']:>5.1f}{flag}")

    _pulse_bridge_report(panel)
    _live_forecast(panel, groups)


def _pulse_bridge_report(panel: pd.DataFrame) -> None:
    """The Pulse is built from the same payroll panel as the headline and is the
    single most promising lever, but only overlaps the headline for a handful of
    months so far. Characterise the relationship; it strengthens each release."""
    print("\n" + "=" * 100)
    print("WEEKLY PULSE -> HEADLINE BRIDGE  (data-starved; grows monthly)")
    print("=" * 100)
    ov = panel.dropna(subset=["y", "pulse_implied"])
    if ov.empty:
        print("  No overlap between Pulse history and observed headlines yet.")
        return
    print(f"  {'month':<10}{'headline':>12}{'pulse_implied':>16}{'pulse_mean':>14}"
          f"{'err':>12}")
    for mth, r in ov.iterrows():
        print(f"  {mth.date()!s:<10}{r['y']:>12,.0f}{r['pulse_implied']:>16,.0f}"
              f"{r['pulse_mean']:>14,.0f}{r['pulse_implied'] - r['y']:>12,.0f}")
    if len(ov) >= 2:
        ratio = (ov["y"] / ov["pulse_implied"]).replace([np.inf, -np.inf], np.nan)
        print(f"\n  mean headline/pulse_implied ratio = {ratio.mean():.2f}  "
              f"(naive bridge assumes 1.00)")
        print(f"  Pulse-implied MAE on overlap = "
              f"{np.mean(np.abs(ov['pulse_implied'] - ov['y'])):,.0f}")


def _live_forecast(panel: pd.DataFrame, groups: dict[str, list[str]]) -> None:
    """Emit a forecast for the next unreleased month using the best full-history
    spec (mom+claims+trends), trained on all observed history."""
    live = panel[panel["y"].isna()]
    if live.empty:
        print("\nNo live (unreleased) month row available.")
        return
    month = live.index.max()
    cols = [c for g in ("momentum", "claims", "trends") for c in groups[g]]
    train = panel[["y", *cols]].dropna(subset=["y", *cols])
    x_now = panel.loc[[month], cols]
    print("\n" + "=" * 100)
    print(f"LIVE FORECAST  -> headline for {month.date()} "
          f"(released ~first Wednesday of {(month + pd.offsets.MonthBegin(1)).date()})")
    print("=" * 100)
    if x_now.isna().any(axis=None):
        missing = x_now.columns[x_now.isna().any()].tolist()
        print(f"  Features not yet complete for {month.date()}: {missing}")
        return
    model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
    model.fit(train[cols].values, train["y"].values)
    pred = float(model.predict(x_now.values)[0])
    print(f"  point forecast (mom+claims+trends): {pred:>+,.0f}")
    if pd.notna(panel.at[month, "pulse_implied"]):
        print(f"  pulse-implied cross-check:          "
              f"{panel.at[month, 'pulse_implied']:>+,.0f}")


if __name__ == "__main__":
    run()
