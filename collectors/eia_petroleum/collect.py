"""EIA petroleum prices + supply collector.

Lands the high-frequency fuel inputs the CPI nowcast and the AAA gasoline
next-day forecast lean on, across two tables:

eia_petroleum.prices
  - weekly U.S. retail gasoline (all grades + regular/midgrade/premium) and
    No. 2 diesel from the "gasoline and diesel" (gnd) dataset;
  - daily WTI and Brent crude spot prices from the spot (spt) dataset;
  - daily U.S. gasoline spot prices (NY Harbor + Gulf Coast conventional
    regular, and LA RBOB regular) from the same spt dataset. These are the
    wholesale-level daily benchmark retail tracks with a lag. (EIA has no usable
    daily NY-Harbor RBOB spot series -- it is unpopulated -- and its monthly
    refiner wholesale/resale gasoline price program was discontinued in 2022-03,
    so RBOB futures, RB=F, are collected separately in collectors/energy_futures.)

eia_petroleum.supply
  - weekly U.S. total motor gasoline ending stocks (stoc/wstk, thousand barrels)
    and refinery percent utilization of operable capacity (pnp/wiup). Supply-side
    fundamentals (a volume and a percentage), kept out of the prices table.

EIA series are not meaningfully revised, so rather than append vintages this
collector UPSERTs on (series_id, observation_date) -- a full-history re-pull each
day leaves each table at a stable one-row-per-series-date size. Every request
needs the free EIA API key (Secret Manager: ``eia-api-key``); the EIA v2 API caps
a response at 5000 rows, so requests are paginated.
"""

import logging
from dataclasses import dataclass, field

from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries
from collectors.common.secrets import get_secret

_log = logging.getLogger(__name__)

EIA_API_URL = "https://api.eia.gov/v2"
PRICES_TABLE = "eia_petroleum.prices"
SUPPLY_TABLE = "eia_petroleum.supply"
EIA_API_KEY_SECRET = "eia-api-key"
START_DATE = "2000-01-01"
PAGE_LEN = 5000  # EIA v2 hard cap per JSON response

SCHEMA: list[bigquery.SchemaField] = [
    # EIA series id, e.g. EMM_EPMR_PTE_NUS_DPG (gasoline) or RWTC (WTI crude)
    bigquery.SchemaField("series_id", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("product", "STRING"),  # EIA product code, e.g. EPM0, EPMR
    bigquery.SchemaField("description", "STRING"),
    bigquery.SchemaField("area", "STRING"),  # EIA duoarea code, e.g. NUS
    bigquery.SchemaField("frequency", "STRING", mode="REQUIRED"),  # weekly | daily
    bigquery.SchemaField("units", "STRING"),  # $/GAL | $/BBL
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
]

MERGE_KEYS = ["series_id", "observation_date"]


@dataclass(frozen=True)
class Query:
    """One EIA v2 data request: a route + frequency + facet filters."""

    route: str
    frequency: str
    facets: dict[str, list[str]] = field(default_factory=dict)


# Daily gasoline spot benchmarks (spt dataset, clean `series` facet). EIA only
# populates daily *conventional* regular spot for NY Harbor + Gulf Coast, plus LA
# RBOB regular; NY-Harbor RBOB is unpopulated. These are the wholesale-level
# daily prices retail tracks with a lag.
GASOLINE_SPOT_SERIES = [
    "EER_EPMRU_PF4_Y35NY_DPG",  # NY Harbor conventional regular
    "EER_EPMRU_PF4_RGC_DPG",  # Gulf Coast conventional regular
    "EER_EPMRR_PF4_Y05LA_DPG",  # Los Angeles reformulated RBOB regular
]

# -> eia_petroleum.prices: weekly retail gasoline + diesel (national), daily
# crude spot benchmarks (RWTC = Cushing WTI, RBRTE = Europe Brent), and daily
# gasoline spot. Gasoline/diesel retail by product + retail process + U.S. area.
PRICE_QUERIES: list[Query] = [
    Query(
        "/petroleum/pri/gnd/data/",
        "weekly",
        {
            "product": ["EPM0", "EPMR", "EPMM", "EPMP", "EPD2D"],
            "process": ["PTE"],
            "duoarea": ["NUS"],
        },
    ),
    Query(
        "/petroleum/pri/spt/data/",
        "daily",
        {"series": ["RWTC", "RBRTE", *GASOLINE_SPOT_SERIES]},
    ),
]

# -> eia_petroleum.supply: weekly U.S. total motor gasoline ending stocks
# (product EPM0, process SAE = Ending Stocks -> series WGTSTUS1, thousand
# barrels) and refinery percent utilization of operable capacity (WPULEUS3, %).
SUPPLY_QUERIES: list[Query] = [
    Query(
        "/petroleum/stoc/wstk/data/",
        "weekly",
        {"product": ["EPM0"], "process": ["SAE"], "duoarea": ["NUS"]},
    ),
    Query("/petroleum/pnp/wiup/data/", "weekly", {"series": ["WPULEUS3"]}),
]


def collect(settings: Settings) -> list[LoadSpec]:
    api_key = get_secret(settings.project_id, EIA_API_KEY_SECRET)
    if not api_key:
        raise RuntimeError(f"EIA API key not found in Secret Manager: {EIA_API_KEY_SECRET}")

    with client() as http:
        price_rows = _run(http, PRICE_QUERIES, api_key)
        supply_rows = _run(http, SUPPLY_QUERIES, api_key)

    return [
        LoadSpec(table=PRICES_TABLE, schema=SCHEMA, rows=price_rows, merge_keys=MERGE_KEYS),
        LoadSpec(table=SUPPLY_TABLE, schema=SCHEMA, rows=supply_rows, merge_keys=MERGE_KEYS),
    ]


def _run(http, queries: list[Query], api_key: str) -> list[dict]:
    rows: list[dict] = []
    for query in queries:
        raw = _fetch(http, query, api_key)
        rows.extend(_row(point, query.frequency) for point in raw)
        _log.info(
            "EIA query fetched",
            extra={"extras": {"route": query.route, "freq": query.frequency, "rows": len(raw)}},
        )
    return rows


def _fetch(http, query: Query, api_key: str) -> list[dict]:
    """Fetch every page of one EIA query, ascending by period (deterministic paging)."""
    collected: list[dict] = []
    offset = 0
    while True:
        params: dict = {
            "api_key": api_key,
            "frequency": query.frequency,
            "data[0]": "value",
            "start": START_DATE,
            "offset": offset,
            "length": PAGE_LEN,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
        }
        for facet, values in query.facets.items():
            for i, value in enumerate(values):
                params[f"facets[{facet}][{i}]"] = value

        def call(params: dict = params) -> dict:
            response = http.get(f"{EIA_API_URL}{query.route}", params=params)
            response.raise_for_status()
            return response.json()

        body = with_retries(call)
        response = body.get("response", {})
        page = response.get("data", [])
        collected.extend(page)

        total = int(response.get("total", 0))
        offset += len(page)
        if not page or offset >= total:
            return collected


def _row(point: dict, frequency: str) -> dict:
    return {
        "series_id": point["series"],
        "product": point.get("product"),
        "description": point.get("series-description"),
        "area": point.get("duoarea"),
        "frequency": frequency,
        "units": point.get("units"),
        "observation_date": point["period"],  # already YYYY-MM-DD
        "value": _parse_float(point.get("value")),
    }


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
