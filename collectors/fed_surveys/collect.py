"""Regional Fed manufacturing survey collector.

Lands the headline seasonally adjusted diffusion index of four regional Fed
manufacturing surveys into ``fed_surveys.manufacturing_surveys``, from each
bank's official full-history file (all verified 2026-06; the HTML landing
pages 403 non-browser clients but the files themselves don't):

  * Empire State (NY Fed, 2001-07+): esms_seasonallyadjusted_diffusion.csv,
    column ``GACDISA`` (current general business conditions); month-end
    dates; literal "ND" for missing.
  * Philly Fed Business Outlook (1968-05+): bos_dif.csv, column ``GAC``;
    "%b-%y" text dates.
  * Richmond Fed (1993-11+): mfg_historicaldata.xlsx, sheet "Mfg Historical
    Series", column ``sa_mfg_composite``.
  * Dallas Fed TMOS (2004-06+): index_sa.xls -- whose extension is a lie
    (the bytes are xlsx; openpyxl, not xlrd) -- sheet "Indexes Seasonally
    Adjusted", column ``Bact`` (general business activity); "%b-%y" text
    dates and ~1k trailing junk rows.

These are +/- balance indexes (NOT the 0-100 PMI scale; the ISM-equivalent
mapping 50 + raw/2 happens downstream). They are the month-M survey inputs
of the ISM Manufacturing forecast (forecasts/ism/manufacturing_pmi): each
releases DURING the survey month (Empire ~15th, Philly ~3rd Thursday,
Richmond ~4th Tuesday, Dallas ~last Monday), before the ISM print on the
1st business day of M+1.

Append-only and vintage-stamped: the banks re-estimate seasonal factors
annually (history restates), so each run re-appends the full files and
consumers dedupe to the latest vintage per (bank, measure, month) via
``ingested_at``.
"""

import io
import logging

import pandas as pd
from google.cloud import bigquery

from collectors.common import LoadSpec, Settings
from collectors.common.http import client, with_retries

_log = logging.getLogger(__name__)

TABLE = "fed_surveys.manufacturing_surveys"
UNITS = "diffusion index (SA, +/- balance)"

EMPIRE_URL = (
    "https://www.newyorkfed.org/medialibrary/media/survey/empire/data/"
    "esms_seasonallyadjusted_diffusion.csv"
)
PHILLY_URL = (
    "https://www.philadelphiafed.org/-/media/frbp/assets/surveys-and-data/"
    "mbos/historical-data/diffusion-indexes/bos_dif.csv"
)
RICHMOND_URL = (
    "https://www.richmondfed.org/-/media/RichmondFedOrg/region_communities/"
    "regional_data_analysis/regional_economy/surveys_of_business_conditions/"
    "manufacturing/data/mfg_historicaldata.xlsx"
)
DALLAS_URL = (
    "https://www.dallasfed.org/~/media/Documents/research/surveys/tmos/documents/index_sa.xls"
)

SCHEMA: list[bigquery.SchemaField] = [
    # empire | philly | richmond | dallas
    bigquery.SchemaField("bank", "STRING", mode="REQUIRED"),
    # general_activity (Empire/Philly/Dallas) | composite (Richmond)
    bigquery.SchemaField("measure", "STRING", mode="REQUIRED"),
    bigquery.SchemaField("observation_month", "DATE", mode="REQUIRED"),
    bigquery.SchemaField("value", "FLOAT64"),
    bigquery.SchemaField("units", "STRING"),
]


def collect(settings: Settings) -> LoadSpec:
    rows: list[dict] = []
    with client(timeout=120.0) as http:
        for bank, fetch_parse in (
            ("empire", _empire),
            ("philly", _philly),
            ("richmond", _richmond),
            ("dallas", _dallas),
        ):
            content = _get(http, fetch_parse.url)
            series = fetch_parse(content)
            bank_rows = [
                {
                    "bank": bank,
                    "measure": fetch_parse.measure,
                    "observation_month": month.date().isoformat(),
                    "value": float(value),
                    "units": UNITS,
                }
                for month, value in series.items()
            ]
            rows.extend(bank_rows)
            _log.info(
                "Fed survey parsed",
                extra={"extras": {"bank": bank, "rows": len(bank_rows)}},
            )
    return LoadSpec(table=TABLE, schema=SCHEMA, rows=rows)


def _get(http, url: str) -> bytes:
    def call() -> bytes:
        response = http.get(url)
        response.raise_for_status()
        return response.content

    return with_retries(call)


def _fix_two_digit_years(months: pd.Series) -> pd.Series:
    """'%b-%y' parses May-68 as 2068 -- surveys can't predate 1950."""
    parsed = pd.to_datetime(months, format="%b-%y", errors="coerce")
    return parsed.where(parsed.dt.year < 2050, parsed - pd.DateOffset(years=100))


def _empire(content: bytes) -> pd.Series:
    frame = pd.read_csv(io.BytesIO(content), na_values=["ND"])
    months = pd.to_datetime(frame["surveyDate"]).dt.to_period("M").dt.to_timestamp()
    values = pd.to_numeric(frame["GACDISA"], errors="coerce")
    return pd.Series(values.to_numpy(), index=months).dropna().sort_index()


_empire.url = EMPIRE_URL
_empire.measure = "general_activity"


def _philly(content: bytes) -> pd.Series:
    frame = pd.read_csv(io.BytesIO(content))
    months = _fix_two_digit_years(frame["DATE"])
    values = pd.to_numeric(frame["GAC"], errors="coerce")
    keep = months.notna() & values.notna()
    return pd.Series(values[keep].to_numpy(), index=months[keep]).sort_index()


_philly.url = PHILLY_URL
_philly.measure = "general_activity"


def _richmond(content: bytes) -> pd.Series:
    frame = pd.read_excel(io.BytesIO(content), sheet_name="Mfg Historical Series")
    months = pd.to_datetime(frame["date"], errors="coerce")
    values = pd.to_numeric(frame["sa_mfg_composite"], errors="coerce")
    keep = months.notna() & values.notna()
    return pd.Series(values[keep].to_numpy(), index=months[keep]).sort_index()


_richmond.url = RICHMOND_URL
_richmond.measure = "composite"


def _dallas(content: bytes) -> pd.Series:
    # The .xls extension is a lie -- the bytes are xlsx (openpyxl engine).
    frame = pd.read_excel(
        io.BytesIO(content), sheet_name="Indexes Seasonally Adjusted", engine="openpyxl"
    )
    months = _fix_two_digit_years(frame["Date"].astype(str))
    values = pd.to_numeric(frame["Bact"], errors="coerce")
    keep = months.notna() & values.notna()
    return pd.Series(values[keep].to_numpy(), index=months[keep]).sort_index()


_dallas.url = DALLAS_URL
_dallas.measure = "general_activity"
