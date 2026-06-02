r"""Dynamic factor model nowcast for NFP + the unemployment rate (the one method
class our feature-regression / MIDAS / tree experiments hadn't tried).

A single mixed-frequency dynamic factor model (statsmodels ``DynamicFactorMQ``,
the Banbura-Modugno EM nowcasting workhorse) extracts a common **labor factor**
from coincident monthly indicators (claims, IUR, ISM Mfg/Svc employment, ADP,
Conference Board labor differential, temp-help). NFP MoM change and the UR change
both load on that factor (opposite signs), so the Kalman filter nowcasts the
not-yet-released target from the concurrent indicators — exactly the Fed-style
nowcast architecture the research review pointed to.

PIT discipline: for origin month M the endog is truncated at M with the two
targets masked at M, so the nowcast uses only data <= M (all coincident
indicators for M are observed by the release origin; NFP/UR for M are not).
COVID months (2020-03..2021-06) are masked from estimation. Parameters are
re-estimated annually (expanding window); the per-origin step only re-runs the
Kalman filter, which is fast.

Caveat: ``standardize=True`` rescales on the endog available at each origin
(<= M, no future leak) but the structural params come from the prior-year fit.

Run:  .\.venv\Scripts\python.exe -m forecasts.bls_employment.dfm
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import harness as nfp_h
from forecasts.bls_employment.unemployment_rate import harness as ur_h

TEST_START = pd.Timestamp("2011-01-01")
PANEL_START = pd.Timestamp("2003-01-01")
COVID_LO, COVID_HI = pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01")
NFP, UR, TEMP = "CES0000000001", "LNS14000000", "CES6056132001"
FACTORS, FACTOR_ORDERS = 2, 2


def build_panel(client) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Monthly stationary indicator panel. Returns (df, ur_level, nfp_level)."""
    bls = data.pull_bls_series([NFP, UR, TEMP], client).set_index("month")
    claims = data.pull_claims_national(client)
    claims["month"] = claims["week_ending"].dt.to_period("M").dt.to_timestamp()
    cm = claims.groupby("month")[["claims_initial_sa", "claims_continued_sa", "iur_sa"]].mean()
    ism = data.pull_ism(client).set_index("month")
    cb = data.pull_conference_board(client).set_index("month")
    adp = data.pull_adp_monthly(client).set_index("month")["adp_sa"]

    # Extend one month past the last BLS print so the live (not-yet-released)
    # month is present with its coincident indicators (targets NaN) for nowcasting.
    idx = pd.date_range(bls.index.min(), bls.index.max() + pd.offsets.MonthBegin(1), freq="MS")
    df = pd.DataFrame(index=idx)
    df["nfp_chg"] = bls[NFP].reindex(idx).diff()
    df["ur_chg"] = bls[UR].reindex(idx).diff()
    df["temp_chg"] = bls[TEMP].reindex(idx).diff()
    df["claims_init_chg"] = cm["claims_initial_sa"].reindex(idx).diff()
    df["claims_cont_chg"] = cm["claims_continued_sa"].reindex(idx).diff()
    df["iur_chg"] = cm["iur_sa"].reindex(idx).diff()
    df["ism_mfg_emp"] = ism["ism_mfg_employment"].reindex(idx)
    df["ism_svc_emp"] = ism["ism_svc_employment"].reindex(idx)
    df["adp_chg"] = adp.reindex(idx).diff() / 1000.0
    df["cb_labor_diff_chg"] = cb["cb_labor_differential"].reindex(idx).diff()

    df = df.loc[PANEL_START:]
    return df, bls[UR].reindex(idx), bls[NFP].reindex(idx)


def _mask_covid(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out[(out.index >= COVID_LO) & (out.index <= COVID_HI)] = np.nan
    return out


def _fit_params(endog: pd.DataFrame):
    model = DynamicFactorMQ(
        endog, factors=FACTORS, factor_orders=FACTOR_ORDERS,
        idiosyncratic_ar1=True, standardize=True,
    )
    return model.fit(disp=False, maxiter=200)


def run() -> None:
    warnings.simplefilter("ignore")
    print("Pulling BigQuery inputs (read-only)...")
    df, ur_level, nfp_level = build_panel(data._client())
    masked = _mask_covid(df)

    test_months = [
        m for m in df.index
        if m >= TEST_START and not (COVID_LO <= m <= COVID_HI) and pd.notna(df.at[m, "nfp_chg"])
    ]
    print(f"DFM: {FACTORS} factor(s), AR({FACTOR_ORDERS}); {len(df.columns)} indicators; "
          f"{len(test_months)} origins {test_months[0].date()}..{test_months[-1].date()}\n")

    params_by_year: dict[int, object] = {}
    rows = []
    for m in test_months:
        yr = m.year
        if yr not in params_by_year:
            train = masked.loc[masked.index < pd.Timestamp(yr, 1, 1)]
            params_by_year[yr] = _fit_params(train).params
        endog = masked.loc[masked.index <= m].copy()
        endog.loc[m, ["nfp_chg", "ur_chg"]] = np.nan
        model = DynamicFactorMQ(
            endog, factors=FACTORS, factor_orders=FACTOR_ORDERS,
            idiosyncratic_ar1=True, standardize=True,
        )
        pred = model.smooth(params_by_year[yr]).predict()
        rows.append({
            "month": m,
            "nfp_true": df.at[m, "nfp_chg"],
            "nfp_pred": pred.at[m, "nfp_chg"],
            "ur_true_level": ur_level.at[m],
            "ur_last": ur_level.at[m - pd.offsets.MonthBegin(1)],
            "ur_pred_level": ur_level.at[m - pd.offsets.MonthBegin(1)] + pred.at[m, "ur_chg"],
        })
    r = pd.DataFrame(rows).dropna()

    print("=" * 104)
    print("DYNAMIC FACTOR MODEL  vs prior best (same-spirit origins)")
    print("=" * 104)
    s_nfp = nfp_h.score(r["nfp_true"].values, r["nfp_pred"].values)
    print("  NFP headline (k):    " + nfp_h._fmt(s_nfp))
    print("    prior best mom+claims (n=168): MAE=82.0  RMSE=106.4")
    s_ur = ur_h.score(r["ur_true_level"].values, r["ur_pred_level"].values, r["ur_last"].values)
    print("  Unemployment rate:   " + ur_h._fmt(s_ur))
    print("    prior best chg:mom+iur+claims(+ism): exact~31%  MAE~0.100")


if __name__ == "__main__":
    run()
