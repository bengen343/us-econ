"""One-shot pull of SA claims (BQ), Google Trends (BQ), and WARN notices (layoffdata.com Google Sheets) into local parquet for offline LGBM/XGBoost experimentation.

Run from repo root:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\pull_data.py

Outputs:
    forecasts\claims\initial_claims\lgbm_test\data\sa_claims.parquet
    forecasts\claims\initial_claims\lgbm_test\data\trends.parquet      (wide)
    forecasts\claims\initial_claims\lgbm_test\data\warn_raw.parquet    (notice-level)
    forecasts\claims\initial_claims\lgbm_test\data\warn_weekly.parquet (effective-week aggregated)
"""

from __future__ import annotations

import io
import pathlib
import sys

import httpx
import pandas as pd
from google.cloud import bigquery

PROJECT = "us-econ-51920"
HERE = pathlib.Path(__file__).parent
DATA = HERE / "data"
DATA.mkdir(exist_ok=True)

WARN_SHEETS = {
    # historical (excludes 2026)
    "historical": "1B1CYZFyJ1ghK1ApuXEeGKo3mLYWzLwONvmWV8Plkav8",
    # current year
    "current": "1q47pIyvmtY7GtF3-7mHOrqBe_0uot_G944XELZ_3raU",
}
SHEET_EXPORT = "https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"


def pull_sa_claims(client: bigquery.Client) -> pd.DataFrame:
    """Pull both the latest-vintage input series AND the first-print actuals.
    fct_sa_input is what production trains on; fct_actuals_as_reported is what
    forecasts are scored against (matches Phase-2 backtest convention)."""
    sql = f"""
    SELECT
      i.week_ending,
      i.value          AS sa_input,
      a.sa_as_reported AS sa_actual
    FROM `{PROJECT}.claims.fct_sa_input` i
    LEFT JOIN `{PROJECT}.claims.fct_actuals_as_reported` a USING (week_ending)
    ORDER BY i.week_ending
    """
    df = client.query(sql).to_dataframe()
    df["week_ending"] = pd.to_datetime(df["week_ending"])
    return df


def pull_adp(client: bigquery.Client) -> pd.DataFrame:
    """ADP weekly NER, wide-format. National U.S. plus a few useful industry
    cuts. ner = level (~132M workers); ner_sa = seasonally adjusted level
    (smoother). For modelling we'll usually want week-over-week diffs."""
    sql = f"""
    SELECT
      observation_date AS week_ending,
      aggregation,
      category,
      ner,
      ner_sa
    FROM `{PROJECT}.adp_employment.ner_history`
    WHERE timestep = 'W'
      AND (
        (aggregation = 'National'   AND category = 'U.S.')
        OR (aggregation = 'Industry' AND category IN (
              'Manufacturing','Construction','Information',
              'Professional and business services','Trade, transportation, and utilities',
              'Leisure and hospitality','Financial activities','Education and health services'
        ))
        OR (aggregation = 'Establishment Size')
      )
    ORDER BY week_ending, aggregation, category
    """
    long_df = client.query(sql).to_dataframe()
    long_df["week_ending"] = pd.to_datetime(long_df["week_ending"])
    # Build short, joinable column names.
    def colname(prefix, agg, cat):
        cat_short = (cat.replace(",", "")
                        .replace(" and ", "_and_")
                        .replace("/", "_")
                        .replace(" ", "_")
                        .replace("__", "_")
                        .lower())
        if agg == "National":
            return f"adp_{prefix}_us"
        if agg == "Industry":
            return f"adp_{prefix}_ind_{cat_short}"
        if agg == "Establishment Size":
            return f"adp_{prefix}_size_{cat_short}"
        return f"adp_{prefix}_{cat_short}"

    parts = []
    for measure in ("ner", "ner_sa"):
        sub = long_df.pivot_table(
            index="week_ending",
            columns=["aggregation", "category"],
            values=measure,
            aggfunc="first",
        )
        sub.columns = [colname(measure, agg, cat) for agg, cat in sub.columns]
        parts.append(sub)
    wide = pd.concat(parts, axis=1).reset_index().sort_values("week_ending")
    return wide


def pull_trends(client: bigquery.Client) -> pd.DataFrame:
    """Long-form -> wide. One row per week, one column per series_id (latest vintage)."""
    sql = f"""
    WITH ranked AS (
      SELECT
        week_ending,
        series_id,
        value,
        ROW_NUMBER() OVER (
          PARTITION BY series_id, week_ending
          ORDER BY vintage_date DESC, is_partial ASC
        ) AS rn
      FROM `{PROJECT}.google_trends.weekly`
    )
    SELECT week_ending, series_id, value
    FROM ranked
    WHERE rn = 1
    """
    long_df = client.query(sql).to_dataframe()
    long_df["week_ending"] = pd.to_datetime(long_df["week_ending"])
    # widen
    wide = long_df.pivot(index="week_ending", columns="series_id", values="value")
    wide.columns = [c.replace("trends.us.", "trends_") for c in wide.columns]
    wide = wide.reset_index().sort_values("week_ending")
    return wide


def fetch_warn_sheet(sid: str) -> pd.DataFrame:
    url = SHEET_EXPORT.format(sid=sid)
    r = httpx.get(url, follow_redirects=True, timeout=60)
    r.raise_for_status()
    return pd.read_csv(io.BytesIO(r.content))


def pull_warn() -> pd.DataFrame:
    parts = []
    for name, sid in WARN_SHEETS.items():
        df = fetch_warn_sheet(sid)
        df["_sheet"] = name
        parts.append(df)
        print(f"  WARN sheet {name}: {len(df):,} rows, cols={list(df.columns)[:8]}...")
    raw = pd.concat(parts, ignore_index=True)
    return raw


def aggregate_warn_weekly(raw: pd.DataFrame) -> pd.DataFrame:
    """Sum affected workers per Saturday-ending week of *effective date*."""
    cols = {c.lower().strip(): c for c in raw.columns}

    # Detect the effective-date and workers columns by fuzzy matching.
    def find(candidates):
        for c in candidates:
            if c in cols:
                return cols[c]
        for key, orig in cols.items():
            for c in candidates:
                if c in key:
                    return orig
        return None

    eff_col = find(["effective", "effective date", "layoff date", "separation"])
    workers_col = find(["affected", "workers", "# workers", "number of workers", "employees"])
    filed_col = find(["filed", "notice date", "received", "warn date", "notice"])

    print(f"  Detected effective={eff_col!r} workers={workers_col!r} filed={filed_col!r}")

    if not eff_col or not workers_col:
        raise RuntimeError(
            f"could not locate effective+workers columns; available: {list(raw.columns)}"
        )

    df = raw[[eff_col, workers_col] + ([filed_col] if filed_col else [])].copy()
    df.columns = ["effective_date", "workers"] + (["filed_date"] if filed_col else [])

    df["effective_date"] = pd.to_datetime(df["effective_date"], errors="coerce")
    if "filed_date" in df.columns:
        df["filed_date"] = pd.to_datetime(df["filed_date"], errors="coerce")
    df["workers"] = pd.to_numeric(df["workers"], errors="coerce")
    df = df.dropna(subset=["effective_date", "workers"])
    df = df[df["workers"] > 0]
    df = df[df["effective_date"] >= "2006-01-01"]

    # Snap to Saturday-ending week (claims convention).
    # week_ending = effective_date + (5 - weekday) % 7; weekday: Mon=0..Sun=6, Sat=5
    wd = df["effective_date"].dt.weekday
    days_to_sat = (5 - wd) % 7
    df["eff_week_ending"] = df["effective_date"] + pd.to_timedelta(days_to_sat, unit="D")

    weekly = (
        df.groupby("eff_week_ending", as_index=False)
          .agg(warn_workers=("workers", "sum"), warn_notices=("workers", "size"))
          .rename(columns={"eff_week_ending": "week_ending"})
          .sort_values("week_ending")
    )
    return weekly


def main():
    client = bigquery.Client(project=PROJECT)

    print("Pulling SA claims ...")
    sa = pull_sa_claims(client)
    sa.to_parquet(DATA / "sa_claims.parquet", index=False)
    n_act = sa["sa_actual"].notna().sum()
    print(f"  -> sa_claims.parquet: {len(sa):,} weeks ({n_act:,} with first-print actuals), {sa['week_ending'].min().date()} .. {sa['week_ending'].max().date()}")

    print("Pulling Google Trends ...")
    tr = pull_trends(client)
    tr.to_parquet(DATA / "trends.parquet", index=False)
    print(f"  -> trends.parquet: {len(tr):,} weeks, {len(tr.columns) - 1} signals")

    print("Pulling ADP weekly NER ...")
    adp = pull_adp(client)
    adp.to_parquet(DATA / "adp_weekly.parquet", index=False)
    print(f"  -> adp_weekly.parquet: {len(adp):,} weeks, {len(adp.columns) - 1} signals  ({adp['week_ending'].min().date()}..{adp['week_ending'].max().date()})")

    print("Pulling WARN sheets ...")
    raw = pull_warn()
    raw.to_parquet(DATA / "warn_raw.parquet", index=False)
    print(f"  -> warn_raw.parquet: {len(raw):,} notice rows")

    weekly = aggregate_warn_weekly(raw)
    weekly.to_parquet(DATA / "warn_weekly.parquet", index=False)
    print(f"  -> warn_weekly.parquet: {len(weekly):,} weeks, {weekly['week_ending'].min().date()} .. {weekly['week_ending'].max().date()}")
    print(f"     median workers/week: {weekly['warn_workers'].median():,.0f}; max: {weekly['warn_workers'].max():,.0f}")


if __name__ == "__main__":
    sys.exit(main())
