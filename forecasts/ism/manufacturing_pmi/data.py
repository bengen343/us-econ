"""Data pulls for the ISM Manufacturing PMI forecast (research harness; the
production sources depend on the bake-off winners).

Target: the headline ISM Manufacturing PMI for month M, released the 1st
business day of M+1 (10:00 ET). At the origin everything surveyed DURING
month M is published:

  * Regional Fed manufacturing surveys for M -- Empire (~15th), Philly
    (~3rd Thursday), Richmond (~4th Tuesday), Dallas (~last Monday).
    Correlations with the ISM PMI run 0.71-0.83 (Richmond Fed, 2024); they
    are diffusion balances (+/-), mapped to the ISM scale as 50 + raw/2.
  * Chicago PMI (~last business day of M, one day before ISM; already on
    the 0-100 PMI scale). Subscriber-gated source (MNI) -- a collection
    question only if it wins.
  * S&P Global US Manufacturing PMI flash (~21st-24th of M; PMI scale).
  * The ISM's own components through M-1 (new orders lead the headline).

Harness sources: the user's hand-maintained ``Sism.xlsm`` workbook (one tab
per survey, dates in col 0 from 1949, headline in col 3; Markit flash in
col 17; maintained through 2025-12) + ``ism.report_on_business`` in BigQuery
for the target and its components (1948+, current).
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT = "us-econ-51920"
WORKBOOK = "Sism.xlsm"

# (panel column, sheet, value column, ISM-scale already?)
WORKBOOK_SERIES = [
    ("chicago", "Chicago PMI", 3, True),
    ("empire", "NY Fed", 3, False),
    ("philly", "Philly Fed", 3, False),
    ("richmond", "Richmond Fed", 3, False),
    ("dallas", "Dallas Fed", 3, False),
    ("flash_mfg", "Markit PMI", 17, True),
    ("markit_final", "Markit PMI", 3, True),
]


def read_workbook_series(xl: pd.ExcelFile, sheet: str, col: int) -> pd.Series:
    frame = pd.read_excel(xl, sheet_name=sheet, header=None)
    dates = pd.to_datetime(frame.iloc[8:, 0], errors="coerce")
    values = pd.to_numeric(frame.iloc[8:, col], errors="coerce")
    keep = dates.notna() & values.notna()
    series = pd.Series(values[keep].to_numpy(), index=dates[keep]).sort_index()
    return series.groupby(level=0).last()  # hand-maintained tabs can repeat a month


def pull_workbook(path: str | Path = WORKBOOK) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    out = {}
    for name, sheet, col, ism_scale in WORKBOOK_SERIES:
        series = read_workbook_series(xl, sheet, col)
        out[name] = series if ism_scale else 50.0 + series / 2.0  # map +/- balance -> PMI scale
    return pd.DataFrame(out)


def pull_ism(client=None) -> pd.DataFrame:
    """ISM manufacturing headline + the leading components from BigQuery,
    latest vintage per month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, measure, value
    FROM `{PROJECT}.ism.report_on_business`
    WHERE report = 'manufacturing'
      AND measure IN ('pmi', 'new_orders', 'production', 'employment')
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY observation_month, measure ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    return frame.pivot(index="observation_month", columns="measure", values="value")


def pull_fed_surveys_bq(client=None) -> pd.DataFrame:
    """Regional Fed survey headlines from BigQuery, latest vintage per month,
    mapped to the ISM scale (50 + raw/2) -- the production path."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT bank, observation_month, value
    FROM `{PROJECT}.fed_surveys.manufacturing_surveys`
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY bank, observation_month ORDER BY ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    frame["observation_month"] = pd.to_datetime(frame["observation_month"])
    wide = frame.pivot(index="observation_month", columns="bank", values="value")
    return 50.0 + wide / 2.0


def pull_flash_mfg_bq(client=None) -> pd.Series:
    """S&P Global US Manufacturing PMI flash from BigQuery (already on the
    PMI scale), latest vintage per month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = f"""
    SELECT observation_month, value
    FROM `{PROJECT}.ism.sp_global_us_pmi`
    WHERE report = 'manufacturing' AND measure = 'pmi' AND release_type = 'flash'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY observation_month ORDER BY ingested_at DESC) = 1
    ORDER BY observation_month
    """
    frame = client.query(sql).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_panel_bq(client=None) -> pd.DataFrame:
    """The production panel: ISM target + Fed surveys + flash, BigQuery only."""
    panel = pull_ism(client).join(pull_fed_surveys_bq(client), how="outer")
    panel = panel.join(pull_flash_mfg_bq(client).rename("flash_mfg"), how="outer")
    panel.index = pd.to_datetime(panel.index)
    return panel.sort_index()


def pull_panel(cache: str | Path | None = None) -> pd.DataFrame:
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    panel = pull_ism().join(pull_workbook(), how="outer")
    panel.index = pd.to_datetime(panel.index)
    panel = panel.sort_index()
    panel.index.name = "month"

    if cache is not None:
        panel.to_csv(cache)
    return panel
