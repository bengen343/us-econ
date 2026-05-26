"""Iteration 6: pull TimesFM 2.5 + ARIMA + ens_w60 h=1 forecasts over the EXACT
97 origins used by the local LGBM walk-forward eval. This gives a definitive
head-to-head and lets us try ensembles.

The production phase-2 SQL is slow (per-origin ARIMA loop ~10-15 min). We use a
similar pattern but only emit h=1 for each origin.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run06_baseline_pull.py
"""

from __future__ import annotations

import pathlib

import pandas as pd
from google.cloud import bigquery

PROJECT = "us-econ-51920"
HERE = pathlib.Path(__file__).resolve().parent
DATA = HERE / "data"

# The 97 origins from the LGBM eval = Saturdays from 2024-07-06 onward where
# both fct_sa_input has a value and fct_actuals_as_reported has the target
# week's value. We let the SQL determine the set so it's truly aligned with
# what the production tables contain right now.

EVAL_START = "2024-07-01"
TRAIN_FLOOR = "2023-01-01"  # matches production / phase-2

SQL_PANEL_AND_FORECAST = f"""
DECLARE eval_start DATE DEFAULT DATE '{EVAL_START}';
DECLARE eval_end   DATE;
SET eval_end = (
  SELECT DATE_SUB(MAX(week_ending), INTERVAL 7 DAY)
  FROM `{PROJECT}.claims.fct_actuals_as_reported`
);

-- Same origin set as the local panel: Saturday weeks where input exists and
-- target week (origin + 7d) has a first-print actual.
CREATE TEMP TABLE origins AS
SELECT i.week_ending AS origin
FROM `{PROJECT}.claims.fct_sa_input` i
JOIN `{PROJECT}.claims.fct_actuals_as_reported` a
  ON a.week_ending = DATE_ADD(i.week_ending, INTERVAL 7 DAY)
WHERE i.week_ending BETWEEN eval_start AND eval_end;

-- Per-origin training panel for AI.FORECAST.
CREATE TEMP TABLE panel_floor AS
SELECT FORMAT_DATE('%Y-%m-%d', o.origin) AS origin,
       TIMESTAMP(s.week_ending) AS ts, s.value AS sa
FROM origins o
JOIN `{PROJECT}.claims.fct_sa_input` s
  ON s.week_ending BETWEEN DATE '{TRAIN_FLOOR}' AND o.origin;

-- TimesFM 2.5 h=1 per origin.
CREATE TEMP TABLE tf AS
SELECT PARSE_DATE('%Y-%m-%d', origin) AS origin,
       DATE(forecast_timestamp) AS target_week,
       forecast_value AS tf25
FROM AI.FORECAST(TABLE panel_floor,
       data_col => 'sa', timestamp_col => 'ts', id_cols => ['origin'],
       model => 'TimesFM 2.5', horizon => 1);

SELECT
  o.origin,
  DATE_ADD(o.origin, INTERVAL 7 DAY) AS target_week,
  act.sa_as_reported AS y_true,
  sn.value           AS snaive,
  tf.tf25
FROM origins o
LEFT JOIN tf ON tf.origin = o.origin
LEFT JOIN `{PROJECT}.claims.fct_actuals_as_reported` act
  ON act.week_ending = DATE_ADD(o.origin, INTERVAL 7 DAY)
LEFT JOIN `{PROJECT}.claims.fct_sa_input` sn
  ON sn.week_ending = DATE_SUB(DATE_ADD(o.origin, INTERVAL 7 DAY), INTERVAL 364 DAY)
ORDER BY o.origin
"""


def main():
    client = bigquery.Client(project=PROJECT)
    print("Pulling TimesFM 2.5 + snaive + actuals over the eval origins ...")
    df = client.query(SQL_PANEL_AND_FORECAST).to_dataframe()
    df["origin"] = pd.to_datetime(df["origin"])
    df["target_week"] = pd.to_datetime(df["target_week"])
    df.to_parquet(DATA / "baselines.parquet", index=False)

    n = len(df)
    have_tf = df["tf25"].notna().sum()
    have_sn = df["snaive"].notna().sum()
    have_y = df["y_true"].notna().sum()

    print(f"  -> baselines.parquet: {n} origins  (have tf={have_tf}, snaive={have_sn}, y_true={have_y})")
    print(f"     origin range: {df['origin'].min().date()} .. {df['origin'].max().date()}")

    # Quick MAE estimate vs the local LGBM
    ok = df.dropna(subset=["y_true", "tf25", "snaive"])
    print()
    print(f"TimesFM 2.5 h=1 MAE on n={len(ok):,} origins: {(ok['tf25'] - ok['y_true']).abs().mean():,.0f}")
    print(f"snaive       h=1 MAE on n={len(ok):,} origins: {(ok['snaive'] - ok['y_true']).abs().mean():,.0f}")


if __name__ == "__main__":
    main()
