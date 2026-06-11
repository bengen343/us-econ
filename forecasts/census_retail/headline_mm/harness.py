r"""Walk-forward bake-off: next-month retail sales headline (MARTS, m/m %).

Target: Delta-log of total retail & food services sales (SA, nominal) for
report month M, forecast just before the ~15th-17th-of-M+1 release. Scored
in m/m % (log-points x100); consensus MAE on this print runs ~0.3-0.4pp.

Method candidates (literature review 2026-06): the public frontier (Chicago
Fed CARTS) nowcasts MARTS from proprietary card/foot-traffic data we cannot
license, but its structure transfers -- the volatile components have
observable month-M drivers, all published before the origin:

  * Light vehicle unit sales (~2nd business day of M+1; autos ~20% of the
    headline).
  * Retail gasoline prices (EIA weekly, fully elapsed; gas stations ~8% --
    nominal sales track the pump price).
  * CPI month M (~10th-13th of M+1 -- ordering vs MARTS was occasionally
    reversed before the 2010s, so dcpi_0 carries a mild PIT caveat in the
    early backtest window; the modern calendar puts CPI first).
  * Michigan sentiment final (4th Friday of M), own-history baselines, and
    a LightGBM challenger.

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests use the latest vintage; advance prints are revised by MRTS one
month later and re-benchmarked annually -- mildly optimistic vs first
prints, noted in the README.

Run: .\.venv\Scripts\python.exe -m forecasts.census_retail.headline_mm.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.census_retail.headline_mm import data
from forecasts.census_retail.headline_mm.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_retail_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2010-01-01")
COVID = (pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01"))


def wf_ols(p: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Expanding-window OLS of y on [1, cols], refit at every origin."""
    y = p["y"].to_numpy()
    X = np.column_stack([np.ones(len(p))] + [p[c].to_numpy() for c in cols])
    trainable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    out = pd.Series(np.nan, index=p.index, dtype=float)
    for i in range(len(p)):
        if not np.isfinite(X[i]).all():
            continue
        train = trainable & (np.arange(len(p)) < i)
        if train.sum() < MIN_TRAIN:
            continue
        beta, *_ = np.linalg.lstsq(X[train], y[train], rcond=None)
        out.iloc[i] = float(X[i] @ beta)
    return out


def wf_lgbm(p: pd.DataFrame, cols: list[str], refit_every: int = 12) -> pd.Series:
    """ML challenger (mirrors the other harnesses)."""
    import lightgbm as lgb

    feats = p[cols].copy()
    feats["month"] = p.index.month
    X = feats.to_numpy()
    y = p["y"].to_numpy()
    trainable = np.isfinite(X).all(axis=1) & np.isfinite(y)
    out = pd.Series(np.nan, index=p.index, dtype=float)
    model = None
    for j, i in enumerate(range(len(p))):
        if not np.isfinite(X[i]).all():
            continue
        train = trainable & (np.arange(len(p)) < i)
        if train.sum() < MIN_TRAIN:
            continue
        if model is None or j % refit_every == 0:
            model = lgb.LGBMRegressor(
                n_estimators=300,
                learning_rate=0.03,
                max_depth=3,
                min_child_samples=20,
                subsample=0.8,
                colsample_bytree=0.8,
                verbosity=-1,
                random_state=7,
            )
            model.fit(X[train], y[train])
        out.iloc[i] = float(model.predict(X[[i]])[0])
    return out


# PIT NOTE (discovered 2026-06-10): BEA's official month-M vehicle SAAR now
# publishes ~the 25th of M+1 -- AFTER the MARTS release -- so dveh_0 is NOT
# point-in-time legal (the start-of-month "auto sales day" died when the
# manufacturers went quarterly; early-month SAARs are private estimators we
# cannot cleanly collect). dveh_0 specs are kept as an upper-bound REFERENCE
# (prefixed x_); production candidates use vehicles at lag 1+ only.
SPECS_OLS: dict[str, list[str]] = {
    "ar1": ["drs_1"],
    "ar2": ["drs_1", "drs_2"],
    "gas": ["dgas_0"],
    "cpi": ["dcpi_0"],
    "veh1": ["dveh_1"],
    "gas_ar": ["dgas_0", "drs_1"],
    "gas_ar2": ["dgas_0", "drs_1", "drs_2"],
    "gas_veh1": ["dgas_0", "dveh_1"],
    "gas_veh1_ar": ["dgas_0", "dveh_1", "drs_1"],
    "gas_cpi_ar": ["dgas_0", "dcpi_0", "drs_1"],
    "gas_veh1_cpi_ar": ["dgas_0", "dveh_1", "dcpi_0", "drs_1"],
    "gas_sent_ar": ["dgas_0", "dsent_0", "drs_1"],
    "kitchen": ["dgas_0", "dgas_1", "dveh_1", "dcpi_0", "drs_1", "drs_2", "dsent_0"],
    "x_veh_gas_ar": ["dveh_0", "dgas_0", "drs_1"],  # NOT PIT-legal -- reference only
    "x_full_nocpi": ["dveh_0", "dgas_0", "drs_1", "drs_2"],  # NOT PIT-legal -- reference
}
LGBM_COLS = ["dveh_1", "dgas_0", "dgas_1", "dcpi_0", "dcpi_1", "drs_1", "drs_2", "dsent_0"]


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["zero"] = 0.0
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    preds["lgbm"] = wf_lgbm(p, LGBM_COLS)
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    d = pd.DataFrame({"y": p["y"], "yhat": yhat})[mask].dropna()
    err = (d["yhat"] - d["y"]) * 100
    hit = np.sign(d["yhat"]) == np.sign(d["y"])
    return {
        "n": len(d),
        "MAE": float(err.abs().mean()),
        "RMSE": float(np.sqrt((err**2).mean())),
        "bias": float(err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def run() -> None:
    print("Pulling MARTS (census.gov) + BEA 7.2.5S + EIA/CPI/Michigan (BigQuery), cached...")
    inputs = data.pull_panel(cache=CACHE)
    p = build_features(inputs)
    print(
        f"Panel: retail n={p['y'].notna().sum()} ({p['retail'].first_valid_index().date()}..)"
        f", vehicles n={p['dveh_0'].notna().sum()}, gas n={p['dgas_0'].notna().sum()}"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)
    mask_2017 = mask & (p.index >= pd.Timestamp("2017-01-01"))

    for label, window_mask in (("2010+", mask), ("2017+ subwindow", mask_2017)):
        scored = {name: score(p, preds[name], window_mask) for name in preds.columns}
        print(f"\n=== RETAIL SALES m/m, % (log-points x100) [{label}] ===\n")
        header = f"  {'method':<14} {'n':>4} {'MAE':>6} {'RMSE':>7} {'bias':>7} {'dir%':>6}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, s in sorted(scored.items(), key=lambda kv: kv[1]["RMSE"]):
            print(
                f"  {name:<14} {s['n']:>4.0f} {s['MAE']:>6.2f} {s['RMSE']:>7.2f} "
                f"{s['bias']:>+7.2f} {s['dir%']:>6.1f}"
            )

    live = forecast_next(inputs)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete -- e.g. vehicles/CPI not yet published)")
    else:
        print(
            f"  {live.target_month.date()}: ${live.level:,.0f}M "
            f"({live.mm_pct:+.2f}% m/m, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
