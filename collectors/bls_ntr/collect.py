"""BLS New Tenant Rent / All Tenant Regressed Rent index — one-time seed loader.

The R-CPI-NTR (new-tenant) and R-CPI-ATR (all-tenant regressed) research indices
are built from the CPI Housing Survey microdata, so they are the cleanest
structural lead of CPI rent (new-tenant rent leads official CPI rent by ~4
quarters). They are NOT in the BLS timeseries API, are published only as an xlsx
on bls.gov (which is bot-protected, 403 to any automated fetch), and BLS paused
publication in April 2026. So there is no live fetch path: instead the published
workbook is bundled in this package (``data/r-cpi-ntr-and-r-cpi-atr.xlsx``) and
parsed here.

Rows are upserted on (index_type, observation_date), so re-running is idempotent
and -- if BLS resumes and the bundled workbook is refreshed -- a redeploy reloads
the new quarters in place. Quarterly frequency; both indices are normalised
around 100 (R-CPI-NTR at 2000q1=100).
"""

import logging
from datetime import date
from importlib.resources import files

import openpyxl
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings

_log = logging.getLogger(__name__)

TABLE = "bls_ntr.rent_index"
WORKBOOK = "data/r-cpi-ntr-and-r-cpi-atr.xlsx"
# Sheet name -> index_type code stored on each row.
SHEETS: dict[str, str] = {"R-CPI-NTR": "R-CPI-NTR", "R-CPI-ATR": "R-CPI-ATR"}
_QUARTER_START_MONTH = {"1": 1, "2": 4, "3": 7, "4": 10}

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("index_type", "STRING", mode="REQUIRED"),  # R-CPI-NTR | R-CPI-ATR
    bigquery.SchemaField("quarter", "STRING", mode="REQUIRED"),  # e.g. 2025q3
    bigquery.SchemaField("observation_date", "DATE", mode="REQUIRED"),  # first day of quarter
    bigquery.SchemaField("index_value", "FLOAT64"),
    bigquery.SchemaField("change_4q", "FLOAT64"),  # year-on-year % change
    bigquery.SchemaField("change_4q_ci_lower", "FLOAT64"),  # 95% CI bounds on the 4q change
    bigquery.SchemaField("change_4q_ci_upper", "FLOAT64"),
]

MERGE_KEYS = ["index_type", "observation_date"]


def collect(settings: Settings) -> LoadSpec:
    path = files("collectors.bls_ntr").joinpath(WORKBOOK)
    with path.open("rb") as handle:
        wb = openpyxl.load_workbook(handle, data_only=True)

    rows: list[dict] = []
    for sheet_name, index_type in SHEETS.items():
        sheet_rows = _parse_sheet(wb[sheet_name], index_type)
        rows.extend(sheet_rows)
        _log.info(
            "NTR sheet parsed",
            extra={"extras": {"sheet": sheet_name, "rows": len(sheet_rows)}},
        )

    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows, merge_keys=MERGE_KEYS)


def _parse_sheet(sheet, index_type: str) -> list[dict]:
    rows: list[dict] = []
    for record in sheet.iter_rows(values_only=True):
        quarter = record[0]
        obs_date = _quarter_to_date(quarter)
        if obs_date is None:
            continue  # title row, header row, trailing note, or blank
        rows.append(
            {
                "index_type": index_type,
                "quarter": quarter.strip(),
                "observation_date": obs_date.isoformat(),
                "index_value": _parse_float(record[1]),
                "change_4q": _parse_float(record[2]),
                "change_4q_ci_lower": _parse_float(record[3]),
                "change_4q_ci_upper": _parse_float(record[4]),
            }
        )
    return rows


def _quarter_to_date(raw: object) -> date | None:
    """Parse a 'YYYYqQ' label to the first day of that quarter; None otherwise."""
    if not isinstance(raw, str):
        return None
    label = raw.strip().lower()
    if len(label) != 6 or label[4] != "q" or label[5] not in _QUARTER_START_MONTH:
        return None
    year = label[:4]
    if not year.isdigit():
        return None
    return date(int(year), _QUARTER_START_MONTH[label[5]], 1)


def _parse_float(raw: object) -> float | None:
    if raw is None or (isinstance(raw, str) and raw.strip() in ("", "-")):
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
