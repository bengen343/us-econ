"""EIA petroleum prices collector.

Lands the high-frequency fuel-price inputs the CPI nowcast leans on: weekly U.S.
retail gasoline (all grades + regular/midgrade/premium) and No. 2 diesel from the
EIA "gasoline and diesel" (gnd) dataset, plus daily WTI and Brent crude spot
prices from the spot (spt) dataset. Gasoline's small CPI weight belies its
outsized month-to-month swing in headline inflation, and daily crude is the
signal that moves a headline nowcast between monthly CPI releases.

EIA prices are not meaningfully revised, so rather than append vintages this
collector UPSERTs on (series_id, observation_date) -- a full-history re-pull each
day leaves the table at a stable one-row-per-series-date size. Every request
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
TABLE = "eia_petroleum.prices"
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


# Weekly retail gasoline + diesel (national), and daily crude spot benchmarks.
# Crude is selected by the clean `series` facet (RWTC = Cushing WTI, RBRTE =
# Europe Brent); gasoline/diesel by product + retail process + U.S. area.
QUERIES: list[Query] = [
    Query(
        "/petroleum/pri/gnd/data/",
        "weekly",
        {
            "product": ["EPM0", "EPMR", "EPMM", "EPMP", "EPD2D"],
            "process": ["PTE"],
            "duoarea": ["NUS"],
        },
    ),
    Query("/petroleum/pri/spt/data/", "daily", {"series": ["RWTC", "RBRTE"]}),
]


def collect(settings: Settings) -> LoadSpec:
    api_key = get_secret(settings.project_id, EIA_API_KEY_SECRET)
    if not api_key:
        raise RuntimeError(f"EIA API key not found in Secret Manager: {EIA_API_KEY_SECRET}")

    rows: list[dict] = []
    with client() as http:
        for query in QUERIES:
            raw = _fetch(http, query, api_key)
            rows.extend(_row(point, query.frequency) for point in raw)
            _log.info(
                "EIA query fetched",
                extra={"extras": {"route": query.route, "freq": query.frequency, "rows": len(raw)}},
            )

    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows, merge_keys=MERGE_KEYS)


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
