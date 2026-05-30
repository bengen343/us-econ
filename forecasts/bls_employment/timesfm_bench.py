r"""TimesFM (BigQuery ``AI.FORECAST``) benchmark for the two Employment Situation
targets, scored on the SAME origins as the sklearn harnesses so the numbers slot
straight into their leaderboards.

TimesFM is univariate, so we forecast the underlying LEVEL series one month ahead
(zero-shot) and derive each target:
  * NFP headline: pred_change(M) = pred_level(M) - actual_level(M-1).
  * Unemployment rate: pred_level(M) directly.

Walk-forward uses the proven ``id_cols`` trick from claims/20_timesfm_test.sql:
one temp panel holds every origin's truncated history tagged by origin, and a
single AI.FORECAST call forecasts h=1 for all of them. Temp tables only — nothing
persistent is written. AI.FORECAST is billed model inference (one call per
target x model), so this is cheap but non-zero.

Run: .\.venv\Scripts\python.exe -m forecasts.bls_employment.timesfm_bench
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from google.cloud import bigquery

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import harness as nfp_h
from forecasts.bls_employment.payrolls_headline import panel as nfp_panel
from forecasts.bls_employment.unemployment_rate import harness as ur_h
from forecasts.bls_employment.unemployment_rate import panel as ur_panel

MODELS = ("TimesFM 2.5", "TimesFM 2.0")
COVID_LO, COVID_HI = "2020-03-01", "2021-06-01"


def timesfm_level(
    client: bigquery.Client, series_id: str, model: str, test_start: str, diff: bool = False
) -> pd.DataFrame:
    """h=1 forecast per origin month (context = all months < origin).

    ``diff=False`` forecasts the level series; ``diff=True`` forecasts the MoM
    first-difference directly (so the prediction IS the change). Returns columns:
    month (origin = target M), target, pred (level or change per ``diff``).
    """
    val_expr = "lvl - LAG(lvl) OVER (ORDER BY month)" if diff else "lvl"
    sql = f"""
    CREATE TEMP TABLE levels AS
    SELECT month, lvl FROM (
      SELECT observation_date AS month, value AS lvl,
             ROW_NUMBER() OVER (PARTITION BY observation_date ORDER BY ingested_at DESC) AS rn
      FROM `{data.PROJECT}.bls_employment.employment_situation`
      WHERE series_id = '{series_id}'
    ) WHERE rn = 1;

    CREATE TEMP TABLE ser AS
    SELECT month, {val_expr} AS val FROM levels;

    CREATE TEMP TABLE origins AS
    SELECT month AS origin FROM levels
    WHERE month >= DATE '{test_start}'
      AND NOT (month BETWEEN DATE '{COVID_LO}' AND DATE '{COVID_HI}');

    CREATE TEMP TABLE panel AS
    SELECT FORMAT_DATE('%Y-%m-%d', o.origin) AS origin,
           TIMESTAMP(s.month) AS ts, s.val AS val
    FROM origins o
    JOIN ser s ON s.month < o.origin AND s.val IS NOT NULL;

    SELECT PARSE_DATE('%Y-%m-%d', origin) AS month,
           DATE(forecast_timestamp) AS target,
           forecast_value AS pred
    FROM AI.FORECAST(TABLE panel,
           data_col => 'val', timestamp_col => 'ts', id_cols => ['origin'],
           model => '{model}', horizon => 1)
    ORDER BY month
    """
    df = client.query(sql).to_dataframe()
    df["month"] = pd.to_datetime(df["month"])
    df["target"] = pd.to_datetime(df["target"])
    return df


def run() -> None:
    c = data._client()

    # Actuals (latest vintage) for both target series.
    bls = data.pull_bls_series([nfp_panel.NFP_TOTAL, ur_panel.UR], c).set_index("month")
    nfp_lvl = bls[nfp_panel.NFP_TOTAL]
    ur_lvl = bls[ur_panel.UR]

    test_start = nfp_h.TEST_START.strftime("%Y-%m-%d")

    for model in MODELS:
        print("\n" + "=" * 96)
        print(f"{model}  (AI.FORECAST, h=1; origins >= {test_start}, COVID-masked)")
        print("=" * 96)

        # ---- NFP headline, level-then-diff: change = pred_level(M) - act(M-1)
        fc = timesfm_level(c, nfp_panel.NFP_TOTAL, model, test_start, diff=False)
        rows = []
        for _, r in fc.iterrows():
            last = nfp_lvl.get(r["month"] - pd.offsets.MonthBegin(1), np.nan)
            true = nfp_lvl.get(r["month"], np.nan)
            rows.append({"y_true": true - last, "pred": r["pred"] - last})
        d = pd.DataFrame(rows).apply(pd.to_numeric, errors="coerce").dropna()
        s = nfp_h.score(d["y_true"].values, d["pred"].values)
        print("  NFP headline, lvl->diff (k): " + nfp_h._fmt(s))

        # ---- NFP headline, change-direct: TimesFM forecasts the MoM change --
        fc = timesfm_level(c, nfp_panel.NFP_TOTAL, model, test_start, diff=True)
        rows = []
        for _, r in fc.iterrows():
            last = nfp_lvl.get(r["month"] - pd.offsets.MonthBegin(1), np.nan)
            true = nfp_lvl.get(r["month"], np.nan)
            rows.append({"y_true": true - last, "pred": r["pred"]})
        d = pd.DataFrame(rows).apply(pd.to_numeric, errors="coerce").dropna()
        s = nfp_h.score(d["y_true"].values, d["pred"].values)
        print("  NFP headline, chg-direct (k):" + nfp_h._fmt(s))

        # ---- Unemployment rate: level directly ------------------------------
        fc = timesfm_level(c, ur_panel.UR, model, test_start, diff=False)
        rows = []
        for _, r in fc.iterrows():
            last = ur_lvl.get(r["month"] - pd.offsets.MonthBegin(1), np.nan)
            true = ur_lvl.get(r["month"], np.nan)
            rows.append({"true": true, "pred": r["pred"], "last": last})
        d = pd.DataFrame(rows).apply(pd.to_numeric, errors="coerce").dropna()
        s = ur_h.score(d["true"].values, d["pred"].values, d["last"].values)
        print("  Unemployment rate:           " + ur_h._fmt(s))


if __name__ == "__main__":
    run()
