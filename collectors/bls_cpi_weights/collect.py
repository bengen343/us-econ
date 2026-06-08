"""CPI relative-importance (cost-weight) seed loader.

The deterministic bottom-up CPI nowcast reconstructs the headline/core from
weighted components, so it needs the CPI relative importances (each item's
percent of all items). Those are NOT in the BLS timeseries API and are published
only as annual xlsx tables on bls.gov, which is bot-blocked. So, like bls_ntr,
the published "Relative importance of components" workbook is bundled in this
package and parsed here.

Only Table 1 (U.S. City Average) is used -- Tables 2-7 are metro/region/size-class
breakdowns. Items are matched by name to the CU item codes tracked in
``bls_cpi`` so the weights join 1:1 to ``bls_cpi.cpi_series``. A single weight
year is a sufficient anchor: the forecast harness derives each month's relative
importance from this base plus the index levels via the BLS price-update identity
(RI_i,t = RI_i,base x index_i,t / index_i,base, renormalised to all items).

Lands in ``bls_cpi.relative_importance`` (same dataset as the index table) and
upserts on (weight_year, population, item_code), so re-running -- or bundling a
newer year's table and redeploying -- is idempotent.
"""

import logging
import re
from importlib.resources import files

import openpyxl
from google.cloud import bigquery

from collectors.bls_cpi.series import CPI_ITEMS
from collectors.common import LoadSpec, Settings

_log = logging.getLogger(__name__)

TABLE = "bls_cpi.relative_importance"
WORKBOOK = "data/cpi-relative-importance.xlsx"
SHEET = "Table 1"  # U.S. City Average
# Table 1 layout: col A = indent, B = item name, C = CPI-U, D = CPI-W.
_COL_ITEM = 1
_POPULATION_COLS = {"CPI-U": 2, "CPI-W": 3}
_TITLE_YEAR = re.compile(r"\((\d{4})\s*Weights\)", re.IGNORECASE)

# Item name (as printed in the RI table) -> CU item code, reusing the curated
# descriptions from the CPI index collector so the two tables align exactly.
_NAME_TO_CODE: dict[str, str] = {description: code for code, description in CPI_ITEMS}

SCHEMA: list[bigquery.SchemaField] = [
    bigquery.SchemaField("weight_year", "INT64", mode="REQUIRED"),
    bigquery.SchemaField("population", "STRING", mode="REQUIRED"),  # CPI-U | CPI-W
    bigquery.SchemaField("item_code", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("item_name", "STRING"),
    bigquery.SchemaField("relative_importance", "FLOAT64"),  # percent of all items
]

MERGE_KEYS = ["weight_year", "population", "item_code"]


def collect(settings: Settings) -> LoadSpec:
    path = files("collectors.bls_cpi_weights").joinpath(WORKBOOK)
    with path.open("rb") as handle:
        wb = openpyxl.load_workbook(handle, data_only=True)
    sheet = wb[SHEET]

    rows_iter = list(sheet.iter_rows(values_only=True))
    weight_year = _weight_year(rows_iter)

    rows: list[dict] = []
    matched: set[str] = set()
    for record in rows_iter:
        name = record[_COL_ITEM]
        if not isinstance(name, str):
            continue
        code = _NAME_TO_CODE.get(name.strip())
        if code is None:
            continue
        if code in matched:
            continue  # some items (e.g. "All items") are restated in the special-aggregates block
        matched.add(code)
        for population, col in _POPULATION_COLS.items():
            ri = _parse_float(record[col] if col < len(record) else None)
            if ri is None:
                continue
            rows.append(
                {
                    "weight_year": weight_year,
                    "population": population,
                    "item_code": code,
                    "item_name": name.strip(),
                    "relative_importance": ri,
                }
            )

    unmatched = {code for code, _ in CPI_ITEMS} - matched
    _log.info(
        "CPI relative importance parsed",
        extra={
            "extras": {
                "weight_year": weight_year,
                "rows": len(rows),
                "items_matched": len(matched),
                "items_unmatched": sorted(unmatched),
            }
        },
    )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows, merge_keys=MERGE_KEYS)


def _weight_year(rows: list[tuple]) -> int:
    for record in rows[:5]:
        for cell in record:
            if isinstance(cell, str):
                match = _TITLE_YEAR.search(cell)
                if match:
                    return int(match.group(1))
    raise RuntimeError("could not find '(YYYY Weights)' in the relative-importance title")


def _parse_float(raw: object) -> float | None:
    if raw is None or (isinstance(raw, str) and raw.strip() in ("", "-")):
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None
