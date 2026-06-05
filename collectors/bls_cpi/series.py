from dataclasses import dataclass

# CPI-U series ID format (see https://www.bls.gov/help/hlpforma.htm):
#   CU + <seasonal> + R + <area> + <item>
#     seasonal: U = not seasonally adjusted, S = seasonally adjusted
#     R       = monthly periodicity
#     area    = 0000 (U.S. city average)
#     item    = the item / aggregate code below
# We track every series in BOTH adjustments because the published headline
# month-over-month change is taken from the SA series while the published
# 12-month (year-over-year) change is taken from the NSA series.
_PREFIX_NSA = "CUUR0000"
_PREFIX_SA = "CUSR0000"


@dataclass(frozen=True)
class CpiSeries:
    series_id: str
    item_code: str
    description: str
    seasonally_adjusted: bool


# (item_code, description). Headline + core are the forecast targets; the rest
# are the components a bottom-up nowcast reconstructs the headline/core from
# (energy/gasoline for the high-frequency headline swing, shelter/OER for the
# dominant core driver, food, used cars, core goods vs core services, etc.).
CPI_ITEMS: list[tuple[str, str]] = [
    # Headline + core (targets)
    ("SA0", "All items"),
    ("SA0L1E", "All items less food and energy"),
    # Top-level splits
    ("SAC", "Commodities"),
    ("SAS", "Services"),
    ("SACL1E", "Commodities less food and energy commodities"),
    ("SASLE", "Services less energy services"),
    # Food
    ("SAF1", "Food"),
    ("SAF11", "Food at home"),
    ("SEFV", "Food away from home"),
    # Energy
    ("SA0E", "Energy"),
    ("SACE", "Energy commodities"),
    ("SETB", "Motor fuel"),
    ("SETB01", "Gasoline (all types)"),
    ("SEHF", "Energy services"),
    ("SEHF01", "Electricity"),
    ("SEHF02", "Utility (piped) gas service"),
    # Shelter
    ("SAH1", "Shelter"),
    ("SEHA", "Rent of primary residence"),
    ("SEHC", "Owners' equivalent rent of residences"),
    # Vehicles
    ("SETA01", "New vehicles"),
    ("SETA02", "Used cars and trucks"),
    # Other notable components
    ("SAA", "Apparel"),
    ("SAM", "Medical care"),
    ("SAM1", "Medical care commodities"),
    ("SAM2", "Medical care services"),
    ("SAS4", "Transportation services"),
    ("SETG01", "Airline fares"),
]


def _build() -> list[CpiSeries]:
    series: list[CpiSeries] = []
    for item_code, description in CPI_ITEMS:
        series.append(CpiSeries(f"{_PREFIX_NSA}{item_code}", item_code, description, False))
        series.append(CpiSeries(f"{_PREFIX_SA}{item_code}", item_code, description, True))
    return series


CPI_SERIES: list[CpiSeries] = _build()
