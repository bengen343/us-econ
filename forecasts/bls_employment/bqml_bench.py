r"""BigQuery-native ML benchmarks (BOOSTED_TREE_REGRESSOR + ARIMA_PLUS_XREG) for
the two Employment Situation targets, scored with the same metrics as the
sklearn harnesses.

These genuinely train models in BigQuery, so unlike the read-only harnesses they
DO write — but only throwaway artifacts in the ``bls_employment`` dataset
(``tmp_bqml_*`` tables + a reused model), dropped at the end. Consistent with the
claims-research precedent (e.g. claims.fct_timesfm_test).

Design (to stay a fair h=1 walk-forward without exploding CREATE MODEL count):
  * BOOSTED_TREE is a plain regressor → **annual retrain**, full 2011+ window.
    A model trained on data < year Y predicts each month of Y from that month's
    own (already-known) features — h=1-equivalent, no leakage.
  * ARIMA_PLUS_XREG forecasts the target series, so a fair h=1 test needs a
    **per-origin retrain**; we run it over a reduced recent window and compare on
    matched origins.

Run: .\.venv\Scripts\python.exe -m forecasts.bls_employment.bqml_bench
"""

from __future__ import annotations

import pandas as pd
from google.cloud import bigquery

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import harness as nfp_h
from forecasts.bls_employment.payrolls_headline import panel as nfp_panel
from forecasts.bls_employment.unemployment_rate import harness as ur_h
from forecasts.bls_employment.unemployment_rate import panel as ur_panel

DATASET = "bls_employment"
MODEL = f"{data.PROJECT}.{DATASET}.tmp_bqml_model"
COVID_LO, COVID_HI = pd.Timestamp("2020-03-01"), pd.Timestamp("2021-06-01")
ARIMA_START = pd.Timestamp("2016-01-01")  # reduced window for per-origin ARIMA


def _write_panel(client: bigquery.Client, df: pd.DataFrame, cols: list[str], table: str) -> str:
    """Load the needed panel columns to a throwaway BQ table. Returns full id."""
    out = df.loc[:, ["month", *cols]].copy()
    out["month"] = pd.to_datetime(out["month"]).dt.date
    full = f"{data.PROJECT}.{DATASET}.{table}"
    client.load_table_from_dataframe(
        out,
        full,
        job_config=bigquery.LoadJobConfig(write_disposition="WRITE_TRUNCATE"),
    ).result()
    return full


def _years(months: list[pd.Timestamp]) -> list[int]:
    return sorted({m.year for m in months})


def boosted_tree_walkforward(
    client: bigquery.Client,
    table: str,
    features: list[str],
    label: str,
    test_months: list[pd.Timestamp],
) -> pd.DataFrame:
    """Annual-retrain BOOSTED_TREE walk-forward. Returns month-indexed `pred`."""
    feat_sql = ", ".join(f"`{f}`" for f in features)
    notnull = " AND ".join(f"`{f}` IS NOT NULL" for f in [*features, label])
    preds: list[pd.DataFrame] = []
    for yr in _years(test_months):
        y0 = f"{yr}-01-01"
        client.query(f"""
            CREATE OR REPLACE MODEL `{MODEL}`
            OPTIONS(model_type='BOOSTED_TREE_REGRESSOR', input_label_cols=['label'],
                    max_tree_depth=4, l2_reg=1.0, subsample=0.85, num_parallel_tree=1) AS
            SELECT {feat_sql}, `{label}` AS label
            FROM `{table}`
            WHERE month < DATE '{y0}'
              AND NOT (month BETWEEN DATE '{COVID_LO.date()}' AND DATE '{COVID_HI.date()}')
              AND {notnull}
        """).result()
        df = client.query(f"""
            SELECT month, predicted_label AS pred
            FROM ML.PREDICT(MODEL `{MODEL}`, (
              SELECT month, {feat_sql} FROM `{table}`
              WHERE EXTRACT(YEAR FROM month) = {yr}
                AND NOT (month BETWEEN DATE '{COVID_LO.date()}' AND DATE '{COVID_HI.date()}')
                AND {notnull.replace(f" AND `{label}` IS NOT NULL", "")}
            ))
            ORDER BY month
        """).to_dataframe()
        preds.append(df)
    out = pd.concat(preds, ignore_index=True)
    out["month"] = pd.to_datetime(out["month"])
    return out.set_index("month")


def run_boosted_tree(client: bigquery.Client) -> None:
    # ---- NFP: predict the MoM change directly --------------------------------
    nfp, ng = nfp_panel.build_panel(
        bls=data.pull_bls_series(nfp_panel.BLS_SERIES, client),
        claims=data.pull_claims_national(client),
        adp=data.pull_adp_monthly(client),
        pulse=data.pull_adp_pulse(client),
        trends=data.pull_trends(client),
        challenger=data.pull_challenger(client),
    )
    nfeat = ng["momentum"] + ng["claims"] + ng["temphelp"]
    nfp_tbl = _write_panel(client, nfp, ["y", *nfeat], "tmp_bqml_panel_nfp")
    nfp_test = [m for m in nfp_h._test_months(nfp)]
    p = boosted_tree_walkforward(client, nfp_tbl, nfeat, "y", nfp_test)
    j = nfp.loc[p.index, ["y"]].join(p).dropna()
    s = nfp_h.score(j["y"].values, j["pred"].values)
    print("BOOSTED_TREE  NFP headline (k):   " + nfp_h._fmt(s))

    # ---- UR: predict the MoM change, reconstruct level -----------------------
    ur, ug = ur_panel.build_panel(
        bls=data.pull_bls_series(ur_panel.BLS_SERIES, client),
        claims=data.pull_claims_national(client),
        trends=data.pull_trends(client),
    )
    ufeat = ug["momentum"] + ug["iur"] + ug["claims"]
    ur_tbl = _write_panel(client, ur, ["y_chg", "y_level", "ur_lag1", *ufeat], "tmp_bqml_panel_ur")
    ur_test = [m for m in ur_h._test_months(ur)]
    p = boosted_tree_walkforward(client, ur_tbl, ufeat, "y_chg", ur_test)
    j = ur.loc[p.index, ["y_level", "ur_lag1"]].join(p).dropna()
    pred_lvl = j["ur_lag1"].values + j["pred"].values
    s = ur_h.score(j["y_level"].values, pred_lvl, j["ur_lag1"].values)
    print("BOOSTED_TREE  Unemployment rate:  " + ur_h._fmt(s))

    for t in (nfp_tbl, ur_tbl):
        client.delete_table(t, not_found_ok=True)
    client.delete_model(MODEL, not_found_ok=True)


def arima_xreg_walkforward(
    client: bigquery.Client,
    table: str,
    xreg: list[str],
    y_col: str,
    origins: list[pd.Timestamp],
) -> pd.DataFrame:
    """Per-origin h=1 ARIMA_PLUS_XREG walk-forward. Returns month-indexed `pred`.

    Trains on all history < M (ARIMA_PLUS auto-cleans the COVID spike), then
    forecasts 1 step with the regressor values AT M (knowable at the origin).
    """
    xreg_sql = ", ".join(f"`{c}`" for c in xreg)
    xreg_nn = " AND ".join(f"`{c}` IS NOT NULL" for c in xreg)
    rows = []
    for m in origins:
        md = m.date()
        client.query(f"""
            CREATE OR REPLACE MODEL `{MODEL}`
            OPTIONS(model_type='ARIMA_PLUS_XREG', time_series_timestamp_col='ts',
                    time_series_data_col='y') AS
            SELECT TIMESTAMP(month) AS ts, `{y_col}` AS y, {xreg_sql}
            FROM `{table}`
            WHERE month < DATE '{md}' AND `{y_col}` IS NOT NULL AND {xreg_nn}
        """).result()
        df = client.query(f"""
            SELECT forecast_value AS pred
            FROM ML.FORECAST(MODEL `{MODEL}`, STRUCT(1 AS horizon),
              (SELECT TIMESTAMP(month) AS ts, {xreg_sql} FROM `{table}`
               WHERE month = DATE '{md}'))
        """).to_dataframe()
        if len(df):
            rows.append({"month": m, "pred": float(df["pred"].iloc[0])})
    out = pd.DataFrame(rows)
    out["month"] = pd.to_datetime(out["month"])
    return out.set_index("month")


def _matched(preds: pd.DataFrame, panel: pd.DataFrame, col: str) -> pd.Series:
    """Align a harness prediction frame's column to the ARIMA origin index."""
    return preds.reindex(panel.index)[col]


def run_arima(client: bigquery.Client) -> None:
    print("\n" + "=" * 104)
    print(
        f"ARIMA_PLUS_XREG  (per-origin h=1 walk-forward, {ARIMA_START.date()}+; "
        "matched-origin Ridge/RW for fairness)"
    )
    print("=" * 104)

    # ---- NFP: ARIMA on the MoM-change series + claims regressors -------------
    nfp, ng = nfp_panel.build_panel(
        bls=data.pull_bls_series(nfp_panel.BLS_SERIES, client),
        claims=data.pull_claims_national(client),
        adp=data.pull_adp_monthly(client),
        pulse=data.pull_adp_pulse(client),
        trends=data.pull_trends(client),
        challenger=data.pull_challenger(client),
    )
    nx = ng["claims"]
    tbl = _write_panel(client, nfp, ["y", *nx], "tmp_bqml_panel_nfp")
    origins = [m for m in nfp_h._test_months(nfp) if m >= ARIMA_START]
    ar = arima_xreg_walkforward(client, tbl, nx, "y", origins)
    common = ar.dropna().index
    rid = nfp_h.walk_forward_model(nfp, ng["momentum"] + ng["claims"])
    base = nfp_h.walk_forward_baselines(nfp)
    y = nfp.loc[common, "y"]
    print("  NFP headline (k):")
    print(
        "    ARIMA_PLUS_XREG      "
        + nfp_h._fmt(nfp_h.score(y.values, ar.loc[common, "pred"].values))
    )
    rid_c = _matched(rid, nfp.loc[common], "pred").dropna()
    print(
        "    Ridge mom+claims     "
        + nfp_h._fmt(nfp_h.score(nfp.loc[rid_c.index, "y"].values, rid_c.values))
    )
    print(
        "    baseline rw          "
        + nfp_h._fmt(nfp_h.score(y.values, base.loc[common, "rw"].values))
    )
    client.delete_table(tbl, not_found_ok=True)

    # ---- UR: ARIMA on the level + IUR/claims regressors ---------------------
    ur, ug = ur_panel.build_panel(
        bls=data.pull_bls_series(ur_panel.BLS_SERIES, client),
        claims=data.pull_claims_national(client),
        trends=data.pull_trends(client),
    )
    ux = ug["iur"] + ug["claims"]
    tbl = _write_panel(client, ur, ["y_level", "ur_lag1", *ux], "tmp_bqml_panel_ur")
    origins = [m for m in ur_h._test_months(ur) if m >= ARIMA_START]
    ar = arima_xreg_walkforward(client, tbl, ux, "y_level", origins)
    common = ar.dropna().index
    rid = ur_h.walk_forward_model(ur, ug["momentum"] + ug["iur"] + ug["claims"], "y_chg")
    base = ur_h.walk_forward_baselines(ur)
    last = ur.loc[common, "ur_lag1"].values
    true = ur.loc[common, "y_level"].values
    print("  Unemployment rate:")
    print(
        "    ARIMA_PLUS_XREG      "
        + ur_h._fmt(ur_h.score(true, ar.loc[common, "pred"].values, last))
    )
    rid_c = rid.reindex(common).dropna(subset=["pred"])
    print(
        "    Ridge chg mom+iur+cl "
        + ur_h._fmt(ur_h.score(rid_c["true"].values, rid_c["pred"].values, rid_c["last"].values))
    )
    print(
        "    baseline rw          "
        + ur_h._fmt(ur_h.score(true, base.loc[common, "rw"].values, last))
    )
    client.delete_table(tbl, not_found_ok=True)

    client.delete_model(MODEL, not_found_ok=True)


def run() -> None:
    client = data._client()
    print("=" * 104)
    print("BOOSTED_TREE_REGRESSOR  (annual-retrain walk-forward, 2011+ COVID-masked)")
    print("=" * 104)
    run_boosted_tree(client)
    run_arima(client)


if __name__ == "__main__":
    run()
