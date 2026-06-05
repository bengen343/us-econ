from dataclasses import dataclass

# PPI Final Demand-Intermediate Demand (FD-ID) commodity series ID format:
#   WP + <seasonal> + FD + <item suffix>
#     seasonal: U = not seasonally adjusted (WPUFD...), S = seasonally adjusted (WPSFD...)
# As with CPI we track both adjustments: the published headline month-over-month
# change comes from the SA series, the 12-month change from the NSA series.
#
# NOTE: the modern *headline* PPI is "Final demand" (suffix "4"). The legacy
# "Final demand-Finished goods" (suffix "49207") is a separate, long-history
# sub-index -- retained here, but it is NOT the headline.
_PREFIX_NSA = "WPUFD"
_PREFIX_SA = "WPSFD"


@dataclass(frozen=True)
class PpiSeries:
    series_id: str
    item_suffix: str
    description: str
    seasonally_adjusted: bool


# (item_suffix, description). The canonical Final Demand aggregation tree:
# final demand = goods + services + construction, with the core (less foods &
# energy) and "core core" (less foods, energy, & trade services) aggregates the
# Fed watches, plus the goods/services subcomponents that map onto CPI food and
# energy for cross-checking the CPI nowcast.
PPI_ITEMS: list[tuple[str, str]] = [
    # Headline + core (targets / key aggregates)
    ("4", "Final demand"),
    ("49104", "Final demand less foods and energy"),
    ("49116", "Final demand less foods, energy, and trade services"),
    # Goods / services / construction split
    ("41", "Final demand goods"),
    ("42", "Final demand services"),
    ("43", "Final demand construction"),
    # Final demand goods subcomponents
    ("411", "Final demand foods"),
    ("412", "Final demand energy"),
    ("413", "Final demand goods less foods and energy"),
    # Final demand services subcomponents
    ("421", "Final demand services less trade, transportation, and warehousing"),
    ("422", "Final demand transportation and warehousing services"),
    ("423", "Final demand trade services"),
    # Legacy finished-goods headline (long history; not the modern headline)
    ("49207", "Final demand-Finished goods"),
]


def _build() -> list[PpiSeries]:
    series: list[PpiSeries] = []
    for suffix, description in PPI_ITEMS:
        series.append(PpiSeries(f"{_PREFIX_NSA}{suffix}", suffix, description, False))
        series.append(PpiSeries(f"{_PREFIX_SA}{suffix}", suffix, description, True))
    return series


PPI_SERIES: list[PpiSeries] = _build()
