r"""Walk-forward bake-off: next-month headline PPI final demand y/y (WPUFD4).

Target: the y/y % of the about-to-be-released month M, forecast just before
the mid-(M+1) PPI release. Only the month-M m/m is unknown (the 12-month base
is published), so every method forecasts Delta-log(NSA index) and the y/y is
recovered arithmetically. Scores are reported in y/y percentage points.

Method candidates (literature review 2026-06):

  * Persistence/seasonality baselines, incl. the y/y random walk (equivalent
    to dlog_M = dlog_{M-12}).
  * FD-ID component lags (energy / foods / core goods / trade / services):
    heterogeneous persistence.
  * ISM prices paid, manufacturing + services, month M (released before the
    PPI print; Cleveland Fed EC 2018-05 found mfg prices has predictive
    content for PPI specifically, corr 0.43 one month ahead).
  * Energy spots dated to the PPI pricing date -- the Tuesday of the week
    containing the 13th -- vs CPI-style complete-month means: gasoline Gulf
    Coast spot, WTI, retail diesel, Henry Hub.
  * Import prices (lag 1; month M releases after the PPI).

COVID months (2020-03..2021-06) are masked from scoring per repo convention.
Backtests use the latest vintage; PPI revises for four months after first
print (caveat: mildly optimistic vs first prints, noted in the README).

Run: .\.venv\Scripts\python.exe -m forecasts.bls_ppi.headline_yy.harness
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_ppi.headline_yy import data
from forecasts.bls_ppi.headline_yy.model import MIN_TRAIN, build_features, forecast_next

CACHE = "_ppi_panel.csv"  # scratch cache; delete to refetch
TEST_START = pd.Timestamp("2017-01-01")  # FD-ID begins 2009-11; MIN_TRAIN=72
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
    """ML challenger (mirrors the eggs harness; expectations low at n~110)."""
    import lightgbm as lgb

    feats = p[cols].copy()
    feats["month"] = p.index.month
    X = feats.to_numpy()  # plain ndarray end-to-end (avoids feature-name warnings)
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


SPECS_OLS: dict[str, list[str]] = {
    "ar1_seas": ["dnsa_1", "seas"],
    "ar2_sa": ["dsa_1", "dsa_2", "seas"],
    "comp": ["denergy_1", "dfoods_1", "dcore_goods_1", "dtrade_1", "seas"],
    "ism_mfg": ["ismm_0", "seas", "dsa_1"],
    "ism_both": ["ismm_0", "isms_0", "seas", "dsa_1"],
    "ism_d": ["ismm_0", "dismm_0", "seas", "dsa_1"],
    "gas_mid": ["dgas_mid_0", "seas", "dsa_1"],
    "gas_avg": ["dgas_avg_0", "seas", "dsa_1"],
    "wti_mid": ["dwti_mid_0", "seas", "dsa_1"],
    "energy_mid": ["dgas_mid_0", "ddiesel_mid_0", "dhh_mid_0", "seas", "dsa_1"],
    "gas_dl": ["dgas_mid_0", "dgas_mid_1", "seas", "dsa_1"],
    "gas_ism": ["dgas_mid_0", "ismm_0", "seas", "dsa_1"],
    "gas_ism_noar": ["dgas_mid_0", "ismm_0", "seas"],
    "gas_only": ["dgas_mid_0", "seas"],
    "kitchen": ["dgas_mid_0", "ddiesel_mid_0", "ismm_0", "dtrade_1", "seas", "dsa_1"],
    "imports": ["dimp_1", "seas", "dsa_1"],
    # Ablations around the dense leaders.
    "gas_diesel": ["dgas_mid_0", "ddiesel_mid_0", "seas", "dsa_1"],
    "gas_dsl_ism": ["dgas_mid_0", "ddiesel_mid_0", "ismm_0", "seas", "dsa_1"],
    "gas_dsl_hh": ["dgas_mid_0", "ddiesel_mid_0", "dhh_mid_0", "seas", "dsa_1"],
    "all_energy_ism": ["dgas_mid_0", "ddiesel_mid_0", "dhh_mid_0", "ismm_0", "seas", "dsa_1"],
    "gas_dsl_noar": ["dgas_mid_0", "ddiesel_mid_0", "seas"],
}


def run_bakeoff(p: pd.DataFrame) -> pd.DataFrame:
    preds = pd.DataFrame(index=p.index)
    preds["rw_yy"] = p["y12"]  # y/y carries forward
    preds["rw_mm"] = p["dnsa_1"]
    preds["zero"] = 0.0
    preds["seasonal"] = p["seas"]
    for name, cols in SPECS_OLS.items():
        preds[name] = wf_ols(p, cols)
    preds["lgbm"] = wf_lgbm(
        p,
        [
            "dgas_mid_0",
            "ddiesel_mid_0",
            "dhh_mid_0",
            "ismm_0",
            "isms_0",
            "dsa_1",
            "dsa_2",
            "dtrade_1",
            "denergy_1",
            "dimp_1",
            "seas",
        ],
    )
    # Score every method on identical months.
    ref = preds[list(SPECS_OLS)].notna().all(axis=1)
    return preds.where(ref, np.nan)


def score(p: pd.DataFrame, yhat: pd.Series, mask: pd.Series) -> dict[str, float]:
    idx = p["nsa_idx"]
    d = pd.DataFrame(
        {
            "yy_act": (idx / idx.shift(12) - 1.0) * 100,
            "yy_hat": (idx.shift(1) * np.exp(yhat) / idx.shift(12) - 1.0) * 100,
            "yy_prev": (idx.shift(1) / idx.shift(13) - 1.0) * 100,
            "mm_err": (yhat - p["y"]) * 100,
        }
    )
    d = d[mask].dropna()
    yy_err = d["yy_hat"] - d["yy_act"]
    # Direction of the y/y CHANGE vs the prior print -- the tradeable call.
    hit = np.sign(d["yy_hat"] - d["yy_prev"]) == np.sign(d["yy_act"] - d["yy_prev"])
    return {
        "n": len(d),
        "yy_MAE": float(yy_err.abs().mean()),
        "yy_RMSE": float(np.sqrt((yy_err**2).mean())),
        "mm_RMSE": float(np.sqrt((d["mm_err"] ** 2).mean())),
        "bias": float(yy_err.mean()),
        "dir%": float(hit.mean() * 100),
    }


def run() -> None:
    print("Pulling BLS (API) + ISM/EIA (BigQuery) + EIA API, cached after first run...")
    panel = data.pull_panel(api_key=data.maybe_api_key(), cache=CACHE)
    p = build_features(panel)
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"(PPI n={panel['nsa_idx'].notna().sum()}, ISM-mfg n={panel['ism_mfg'].notna().sum()}, "
        f"gas-mid n={panel['gas_mid'].notna().sum()}, imports n={panel['imports'].notna().sum()})"
    )

    preds = run_bakeoff(p)
    is_covid = (p.index >= COVID[0]) & (p.index <= COVID[1])
    mask = pd.Series((p.index >= TEST_START) & ~is_covid, index=p.index)

    for label, window_mask in (
        ("full 2017+", mask),
        ("2022+ subwindow", mask & (p.index >= pd.Timestamp("2022-01-01"))),
    ):
        scored = {name: score(p, preds[name], window_mask) for name in preds.columns}
        test_months = preds[window_mask].dropna(how="all").index
        print(
            f"\n[{label}] {test_months.min().date()}..{test_months.max().date()}, "
            f"COVID-masked. y/y + m/m errors in percentage points.\n"
        )
        header = (
            f"  {'method':<14} {'n':>4} {'yy_MAE':>7} {'yy_RMSE':>8} "
            f"{'mm_RMSE':>8} {'bias':>7} {'dir%':>6}"
        )
        print(header)
        print("  " + "-" * (len(header) - 2))
        for name, s in sorted(scored.items(), key=lambda kv: kv[1]["yy_RMSE"]):
            print(
                f"  {name:<14} {s['n']:>4.0f} {s['yy_MAE']:>7.2f} {s['yy_RMSE']:>8.2f} "
                f"{s['mm_RMSE']:>8.2f} {s['bias']:>+7.2f} {s['dir%']:>6.1f}"
            )

    live = forecast_next(panel)
    print("\nLIVE NOWCAST (production spec)")
    if live is None:
        print("  none (target-month regressors incomplete).")
    else:
        print(
            f"  {live.target_month.date()}: index {live.level:.3f} "
            f"({live.mm_pct:+.2f}% m/m, {live.yy_pct:+.2f}% y/y, n_train={live.n_train})"
        )


if __name__ == "__main__":
    run()
