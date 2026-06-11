"""Census Advance Monthly Retail Trade Survey (MARTS) collector.

Lands the advance retail & food services sales estimates (SA, $M, 1992+) for
every published kind-of-business line into
``census_retail.advance_retail_sales``, parsed from the per-NAICS advance
time-series txt files on census.gov (keyless; the EITS API now requires a
registered key):

    https://www.census.gov/retail/marts/www/adv<CODE>.txt

Each file is a title line, a YEAR/JAN..DEC header, year rows of SA estimates,
then a SEASONAL FACTORS block (not ingested -- the headline m/m is computed
from the SA levels). Filenames are case-sensitive (e.g. adv44X72.txt).

``44X72`` (retail & food services, total) is the headline the retail-sales
forecast targets (forecasts/census_retail/headline_mm). Append-only and
vintage-stamped: advance estimates are revised in place by MRTS one month
later and re-benchmarked annually, and the release lands ~the 15th-17th of
M+1 (08:30 ET, ~10 business days after month end), so the job runs daily
through that window; consumers dedupe to the latest vintage per
(naics_code, month) via ``ingested_at`` and first prints accrue for revision
studies.
"""

import logging

from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "census_retail.advance_retail_sales"
URL_FMT = "https://www.census.gov/retail/marts/www/adv{code}.txt"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Every advance series published on the MARTS time-series page (case matters).
CODES = [
    "44X72",  # retail & food services, total -- the headline
    "44W72",  # ... excl motor vehicle & parts
    "44Y72",  # ... excl gasoline stations
    "44Z72",  # ... excl motor vehicle & parts and gasoline stations
    "44000",  # retail, total
    "4400A",  # retail, total excl motor vehicle & parts
    "44100",  # motor vehicle & parts dealers
    "441X0",  # auto & other motor vehicle dealers
    "44200",  # furniture & home furnishings
    "44300",  # electronics & appliances
    "44400",  # building materials & garden equipment
    "44500",  # food & beverage stores
    "44510",  # grocery stores
    "44600",  # health & personal care
    "44700",  # gasoline stations
    "44800",  # clothing & accessories
    "45100",  # sporting goods, hobby, musical instrument, & book
    "45200",  # general merchandise
    "45220",  # department stores
    "45300",  # miscellaneous store retailers
    "45400",  # nonstore retailers
    "72200",  # food services & drinking places
]

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("naics_code", "STRING", mode="REQUIRED"),  # e.g. 44X72
    bigquery.SchemaField("description", "STRING"),  # the file's title line
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]
UNITS = "millions of dollars (SA)"


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client(timeout=120.0) as http:
        for code in CODES:

            def call(code: str = code) -> str:
                response = http.get(URL_FMT.format(code=code), headers={"User-Agent": BROWSER_UA})
                response.raise_for_status()
                return response.text

            text = with_retries(call)
            series_rows = _parse_txt(text, code)
            rows.extend(series_rows)
            _log.info(
                "MARTS series parsed",
                extra={"extras": {"code": code, "rows": len(series_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _parse_txt(text: str, code: str) -> list[dict]:
    """The SA estimates block: title line, YEAR/JAN..DEC header, year rows
    until the SEASONAL FACTORS block. '(NA)' and gaps are skipped."""
    lines = text.splitlines()
    description = lines[0].strip() if lines else None
    rows: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("SEASONAL FACTORS"):
            break
        parts = stripped.split()
        if not parts or not parts[0].isdigit() or not 1990 <= int(parts[0]) <= 2100:
            continue
        year = int(parts[0])
        for m, raw in enumerate(parts[1:13]):
            try:
                value = float(raw)
            except ValueError:
                continue  # (NA) etc.
            rows.append(
                {
                    "naics_code": code,
                    "description": description,
                    "observation_month": f"{year:04d}-{m + 1:02d}-01",
                    "value": value,
                    "units": UNITS,
                }
            )
    return rows
