r"""Bake-off: does any fitted model beat the deterministic DMS reconstruction?

Compares the champion bottom-up reconstruction (``dms``) against the three method
classes the research flagged, on the SAME COVID-masked headline-m/m origins:

  * Ridge bridge   -- fit coefficients on the very inputs the DMS identity
                      combines by accounting (core/food/energy-services trailing
                      averages + the deseasonalised gasoline nowcast + oil +
                      headline momentum). Tests "does fitting beat the identity?"
  * U-MIDAS        -- the K most recent *weekly* gasoline prints at native
                      frequency (Ridge over the collinear lags), vs the monthly
                      mean the DMS uses. Tests "does intra-month fuel timing help?"
  * Dynamic factor -- a DynamicFactorMQ common inflation factor over headline +
                      core/food m/m + gasoline/oil, target masked at the origin.

PIT discipline matches the DMS harness: month M is nowcast at its ~mid-M+1
release, so month M's complete fuel prices and CPI prints through M-1 are
knowable. Expanding-window fits (Ridge/MIDAS refit per origin; DFM params refit
annually, Kalman-smoothed per origin). Read-only.

Run: .\.venv\Scripts\python.exe -m forecasts.bls_cpi.bench
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from forecasts.bls_cpi import data
from forecasts.bls_cpi.dms import harness as dms_h
from forecasts.bls_cpi.dms import panel as panel_mod

TEST_START = pd.Timestamp("2010-01-01")
MIN_TRAIN = 48
ALPHAS = (0.01, 0.03, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0)
COVID_LO, COVID_HI = pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01")
K_WEEKS = 6  # weekly gasoline lags for U-MIDAS (~one month of weeks + a little)
TARGET = "all_sa_mm"


# --------------------------------------------------------------------------- #
# Feature frame (PIT-clean for nowcasting completed month M)
# --------------------------------------------------------------------------- #
def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel
    x = pd.DataFrame(index=p.index)
    x["core_trail"] = dms_h._trailing12(p["core_sa_mm"])
    x["food_trail"] = dms_h._trailing12(p["food_sa_mm"])
    x["enserv_trail"] = dms_h._trailing12(p["enserv_sa_mm"])
    gap = p["gas_nsa_mm"] - p["gas_sa_mm"]
    gas_seas = gap.groupby(p.index.month).transform(lambda s: s.shift(1).expanding().mean())
    x["gas_sa_hat"] = p["eia_gas_mm"] - gas_seas  # deseasonalised gasoline (month M, complete)
    x["wti_mm"] = p["wti_mm"]
    x["hl_lag1"] = p[TARGET].shift(1)
    x["hl_lag2"] = p[TARGET].shift(2)
    x["y"] = p[TARGET]
    x["is_covid"] = p["is_covid"]
    return x


def _test_months(x: pd.DataFrame) -> list[pd.Timestamp]:
    return [
        m
        for m in x.index
        if m >= TEST_START and not (COVID_LO <= m <= COVID_HI) and pd.notna(x.at[m, "y"])
    ]


def walk_forward_ridge(x: pd.DataFrame, cols: list[str]) -> pd.Series:
    """Expanding-window RidgeCV, refit per origin, COVID excluded from training."""
    obs = x[~x["is_covid"]]
    preds = {}
    for m in _test_months(x):
        train = obs.loc[obs.index < m, [*cols, "y"]].dropna()
        x_now = x.loc[[m], cols]
        if len(train) < MIN_TRAIN or x_now.isna().any(axis=None):
            continue
        model = make_pipeline(StandardScaler(), RidgeCV(alphas=ALPHAS))
        model.fit(train[cols].to_numpy(), train["y"].to_numpy())
        preds[m] = float(model.predict(x_now.to_numpy())[0])
    return pd.Series(preds, name="pred")


# --------------------------------------------------------------------------- #
# U-MIDAS: weekly gasoline at native frequency
# --------------------------------------------------------------------------- #
def weekly_gas_lags(client, months: pd.DatetimeIndex, k: int) -> pd.DataFrame:
    """K most recent weekly gasoline % changes with week_ending <= end of month M."""
    df = client.query(
        f"""SELECT observation_date, value FROM `{data.PROJECT}.eia_petroleum.prices`
            WHERE series_id = 'EMM_EPM0_PTE_NUS_DPG' ORDER BY observation_date"""
    ).to_dataframe()
    df["observation_date"] = pd.to_datetime(df["observation_date"])
    we = df["observation_date"].to_numpy()
    chg = (df["value"].pct_change() * 100).to_numpy()
    out = {f"gas_w{j}": np.full(len(months), np.nan) for j in range(k)}
    for i, m in enumerate(months):
        m_end = np.datetime64((m + pd.offsets.MonthEnd(0)).date())
        pos = int(np.searchsorted(we, m_end, side="right"))
        if pos >= k:
            window = chg[pos - k : pos][::-1]
            for j in range(k):
                out[f"gas_w{j}"][i] = window[j]
    return pd.DataFrame(out, index=months)


# --------------------------------------------------------------------------- #
# Dynamic factor model
# --------------------------------------------------------------------------- #
def dfm_nowcast(panel: pd.DataFrame, test_months: list[pd.Timestamp]) -> pd.Series:
    from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

    # NB: exclude food -- headline = core + food + energy is an exact identity, so
    # including all of them makes the endog rank-deficient (singular EM step).
    endog_cols = ["all_sa_mm", "core_sa_mm", "eia_gas_mm", "wti_mm"]
    endog = panel[endog_cols].copy()
    endog[(endog.index >= COVID_LO) & (endog.index <= COVID_HI)] = np.nan

    def fit(train):
        return DynamicFactorMQ(
            train, factors=1, factor_orders=2, idiosyncratic_ar1=True, standardize=True
        ).fit(disp=False, maxiter=200)

    params_by_year: dict[int, object] = {}
    preds = {}
    for m in test_months:
        yr = m.year
        if yr not in params_by_year:
            train = endog.loc[endog.index < pd.Timestamp(yr, 1, 1)].dropna(how="all")
            params_by_year[yr] = fit(train).params
        e = endog.loc[endog.index <= m].copy()
        e.loc[m, "all_sa_mm"] = np.nan  # mask the target at the origin
        model = DynamicFactorMQ(
            e, factors=1, factor_orders=2, idiosyncratic_ar1=True, standardize=True
        )
        preds[m] = float(model.smooth(params_by_year[yr]).predict().at[m, "all_sa_mm"])
    return pd.Series(preds, name="pred")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> None:
    warnings.simplefilter("ignore")
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    panel = panel_mod.build_panel(data.pull_cpi(c), data.pull_eia_monthly(c), data.pull_manheim(c))
    weights, weight_year = data.pull_cpi_weights(c)
    x = build_features(panel)
    tm = _test_months(x)

    # Champion: DMS reconstruction (headline m/m).
    dms_pred = dms_h.build_forecasts(panel, weights, weight_year)["headline_mm"]

    # Comparators.
    dms_cols = ["core_trail", "food_trail", "enserv_trail", "gas_sa_hat"]
    ridge_dms = walk_forward_ridge(x, dms_cols)
    ridge_plus = walk_forward_ridge(x, [*dms_cols, "wti_mm", "hl_lag1", "hl_lag2"])
    xm = x.copy()
    for name, col in weekly_gas_lags(c, x.index, K_WEEKS).items():
        xm[name] = col
    midas = walk_forward_ridge(
        xm, ["core_trail", "food_trail", *[f"gas_w{j}" for j in range(K_WEEKS)]]
    )
    print("Fitting dynamic factor model (annual re-estimation; this takes a moment)...")
    dfm = dfm_nowcast(panel, tm)

    actual = x["y"]
    methods = {
        "DMS reconstruction (champion)": dms_pred,
        "Ridge bridge (DMS inputs)": ridge_dms,
        "Ridge + oil + momentum": ridge_plus,
        f"U-MIDAS (weekly gas x{K_WEEKS})": midas,
        "Dynamic factor (DFM)": dfm,
    }

    # Score on the common set of origins where every method has a prediction.
    common = set(tm)
    for s in methods.values():
        common &= set(s.dropna().index)
    common = sorted(common)
    a = actual.loc[common]

    print(f"\nHeadline m/m (SA), {len(common)} common origins "
          f"{common[0].date()}..{common[-1].date()} (>= {TEST_START.date()}, COVID-masked)\n")
    print("=" * 84)
    scored = [(name, dms_h.score(a, s.loc[common])) for name, s in methods.items()]
    for name, sc in sorted(scored, key=lambda kv: kv[1]["RMSE"]):
        mark = "  <-- champion" if name.startswith("DMS") else ""
        print(dms_h._fmt(name, sc) + mark)


if __name__ == "__main__":
    run()
