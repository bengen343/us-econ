"""Data pulls for the headline-PPI y/y forecast (research harness + production).

The target is the y/y % change of PPI Final Demand, computed (per BLS
convention) from the NSA index ``WPUFD4``. At the origin -- just before the
mid-(M+1) PPI release -- the index is published through M-1, so the only
unknown in y/y(M) is the month-M m/m change; the 12-month base is arithmetic:

    yy_M = I_{M-1} * exp(dlog_M) / I_{M-12} - 1

Candidate inputs and their point-in-time status at the origin:

  * Own history (headline NSA/SA + FD-ID components, SA): published through
    M-1 -- lags >= 1.
  * ISM prices paid (manufacturing 1949+, services 1997+): the month-M survey
    is released on the 1st/3rd business day of M+1, BEFORE the PPI release --
    month M usable (lag 0). Pulled from our BigQuery (``ism.report_on_business``).
  * Energy spots: PPI prices reference ONE day -- the Tuesday of the week
    containing the 13th (range 9th..15th) -- so the natural regressor is the
    pricing-date-to-pricing-date change, not the complete-month mean (the CPI
    convention). Gasoline Gulf Coast spot / WTI (daily, BigQuery
    ``eia_petroleum.prices``), diesel retail (weekly Mondays, same table),
    Henry Hub (daily, EIA API ``RNGWHHD`` -- FRED's keyless CSV endpoint was
    down 2026-06-10; the EIA key is already in Secret Manager). Month-M
    pricing dates are fully observed at the origin -- lag 0 PIT-clean.
  * Import prices (all commodities NSA, BLS ``EIUIR``): month M releases
    AFTER the PPI (e.g. 2026-06-16 vs -11) -- lags >= 1 only.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from collectors.common.bls import fetch_series, parse_value

PROJECT = "us-econ-51920"

PPI_HEADLINE_NSA = "WPUFD4"
PPI_HEADLINE_SA = "WPSFD4"
# FD-ID components (SA m/m is the published cut), all back to 2009-11.
PPI_COMPONENTS_SA = {
    "energy": "WPSFD412",
    "foods": "WPSFD411",
    "core_goods": "WPSFD413",  # goods less foods and energy
    "trade": "WPSFD423",  # trade services (margins)
    "svc_xtrade": "WPSFD421",  # services less trade, transport, warehousing
}

EIA_GAS_SPOT = "EER_EPMRU_PF4_RGC_DPG"  # conventional regular, Gulf Coast, daily
EIA_WTI = "RWTC"  # WTI Cushing spot, daily
EIA_DIESEL_WEEKLY = "EMD_EPD2D_PTE_NUS_DPG"  # No. 2 diesel retail, weekly Mondays
EIA_HENRY_HUB = "RNGWHHD"  # Henry Hub natural gas spot, daily (EIA API)
BLS_IMPORT_PRICES = "EIUIR"  # import price index, all commodities, NSA monthly

_START_YEAR = 2009  # FD-ID aggregates begin 2009-11


def ppi_pricing_date(month: pd.Timestamp) -> pd.Timestamp:
    """The PPI pricing date for a month: Tuesday of the (Sun..Sat) week
    containing the 13th. Lands on the 9th..15th."""
    the_13th = month.replace(day=13)
    sunday = the_13th - timedelta(days=(the_13th.weekday() + 1) % 7)
    return pd.Timestamp(sunday + timedelta(days=2))


def monthly_at_pricing_date(obs: pd.Series, max_lookback_days: int = 6) -> pd.Series:
    """Per-month value of a daily/weekly series at the PPI pricing date,
    using the last observation on or before that Tuesday (markets shut on
    holidays; weekly Monday series land one day prior)."""
    obs = obs.dropna().sort_index()
    out: dict[pd.Timestamp, float] = {}
    for month in pd.period_range(obs.index.min(), obs.index.max(), freq="M"):
        stamp = month.to_timestamp()
        window = obs.loc[
            ppi_pricing_date(stamp) - timedelta(days=max_lookback_days) : ppi_pricing_date(stamp)
        ]
        if len(window):
            out[stamp] = float(window.iloc[-1])
    return pd.Series(out, dtype=float).sort_index()


def monthly_mean_complete(obs: pd.Series, max_gap_days: int = 13) -> pd.Series:
    """Calendar-month means with the repo's complete-month guard (the CPI-style
    aggregation, kept as a bake-off comparator for the pricing-date variant)."""
    frame = obs.dropna().to_frame("value")
    frame["month"] = frame.index.to_period("M").to_timestamp()
    grouped = frame.groupby("month")["value"]
    first_day = frame.groupby("month").apply(lambda g: g.index.min().day)
    last_gap = frame.groupby("month").apply(
        lambda g: (g.index.max().to_period("M").to_timestamp("M") - g.index.max()).days
    )
    complete = (first_day <= max_gap_days) & (last_gap <= max_gap_days - 1)
    return grouped.mean()[complete]


def _points_to_series(points: list[dict]) -> pd.Series:
    rows = {}
    for p in points:
        if not p["period"].startswith("M") or p["period"] == "M13":
            continue
        rows[pd.Timestamp(int(p["year"]), int(p["period"][1:]), 1)] = parse_value(p.get("value"))
    return pd.Series(rows, dtype=float).sort_index()


def pull_henry_hub() -> pd.Series:
    """Henry Hub daily spot ($/MMBtu) straight from the EIA v2 API (paginated;
    the natural-gas dataset is not in our ``eia_petroleum`` tables)."""
    from collectors.common.http import client, with_retries
    from collectors.common.secrets import get_secret

    api_key = get_secret(PROJECT, "eia-api-key")
    rows: dict[pd.Timestamp, float] = {}
    offset = 0
    with client() as http:
        while True:
            params = {
                "api_key": api_key,
                "frequency": "daily",
                "data[0]": "value",
                "facets[series][0]": EIA_HENRY_HUB,
                "start": "2000-01-01",
                "offset": offset,
                "length": 5000,
                "sort[0][column]": "period",
                "sort[0][direction]": "asc",
            }

            def call(params: dict = params) -> dict:
                response = http.get(
                    "https://api.eia.gov/v2/natural-gas/pri/fut/data/", params=params
                )
                response.raise_for_status()
                return response.json()

            body = with_retries(call)["response"]
            for point in body.get("data", []):
                if point.get("value") is not None:
                    rows[pd.Timestamp(point["period"])] = float(point["value"])
            offset += len(body.get("data", []))
            if not body.get("data") or offset >= int(body.get("total", 0)):
                break
    return pd.Series(rows, dtype=float).sort_index()


def pull_eia_series(series_id: str, client=None) -> pd.Series:
    """A daily/weekly EIA price series from BigQuery (clean upserted table)."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = """
    SELECT observation_date, value
    FROM `us-econ-51920.eia_petroleum.prices`
    WHERE series_id = @sid
    ORDER BY observation_date
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("sid", "STRING", series_id)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_date"])
    )


def pull_ism_prices(report: str, client=None) -> pd.Series:
    """ISM prices-paid diffusion index (monthly) from BigQuery, latest release
    per observation month."""
    from google.cloud import bigquery

    client = client or bigquery.Client(project=PROJECT)
    sql = """
    SELECT observation_month, value
    FROM `us-econ-51920.ism.report_on_business`
    WHERE report = @report AND measure = 'prices'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY observation_month ORDER BY release_month DESC, ingested_at DESC
    ) = 1
    ORDER BY observation_month
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ScalarQueryParameter("report", "STRING", report)]
    )
    frame = client.query(sql, job_config=job_config).to_dataframe()
    return pd.Series(
        frame["value"].to_numpy(dtype=float), index=pd.to_datetime(frame["observation_month"])
    )


def pull_panel(api_key: str | None = None, cache: str | Path | None = None) -> pd.DataFrame:
    """Monthly panel for the harness: headline indexes, SA components, ISM
    prices, energy spots at the pricing date and as complete-month means,
    import prices."""
    if cache is not None and Path(cache).exists():
        return pd.read_csv(cache, index_col=0, parse_dates=True)

    bls_ids = [PPI_HEADLINE_NSA, PPI_HEADLINE_SA, *PPI_COMPONENTS_SA.values(), BLS_IMPORT_PRICES]
    by_series = fetch_series(bls_ids, _START_YEAR, date.today().year, api_key=api_key)

    gas = pull_eia_series(EIA_GAS_SPOT)
    wti = pull_eia_series(EIA_WTI)
    diesel = pull_eia_series(EIA_DIESEL_WEEKLY)
    henry = pull_henry_hub()

    panel = pd.DataFrame(
        {
            "nsa_idx": _points_to_series(by_series[PPI_HEADLINE_NSA]),
            "sa_idx": _points_to_series(by_series[PPI_HEADLINE_SA]),
            **{name: _points_to_series(by_series[sid]) for name, sid in PPI_COMPONENTS_SA.items()},
            "ism_mfg": pull_ism_prices("manufacturing"),
            "ism_svc": pull_ism_prices("services"),
            "gas_mid": monthly_at_pricing_date(gas),
            "wti_mid": monthly_at_pricing_date(wti),
            "diesel_mid": monthly_at_pricing_date(diesel),
            "hh_mid": monthly_at_pricing_date(henry),
            "gas_avg": monthly_mean_complete(gas),
            "wti_avg": monthly_mean_complete(wti),
            "imports": _points_to_series(by_series[BLS_IMPORT_PRICES]),
        }
    ).sort_index()
    panel.index.name = "month"

    if cache is not None:
        panel.to_csv(cache)
    return panel


def maybe_api_key() -> str | None:
    """Best-effort BLS API key from Secret Manager (higher quota); None offline."""
    try:
        from collectors.common.secrets import get_secret

        return get_secret(PROJECT, "bls-api-key")
    except Exception:
        return None
