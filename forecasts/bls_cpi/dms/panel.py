"""Monthly panel for the Cleveland-Fed-style bottom-up CPI reconstruction.

One row per month M (first-of-month Timestamp), extended one month past the last
CPI release so the harness can emit a live nowcast. Carries the published
actuals (targets + components, SA and NSA) and the high-frequency gasoline
signal; the deterministic nowcasts themselves are computed in ``harness``.

PIT timing model
----------------
We nowcast month M for its release ~mid-M+1. By that origin month M has fully
elapsed, so the entire month's EIA gasoline/oil prints are knowable, and CPI
component prints through M-1 are published. Everything used here respects that.
"""

from __future__ import annotations

import pandas as pd


def build_panel(cpi: pd.DataFrame, eia: pd.DataFrame) -> pd.DataFrame:
    """Join CPI (wide, latest vintage) with monthly fuel prices; add the gasoline
    monthly price change and a COVID mask; extend one month for the live nowcast."""
    panel = cpi.set_index("month").sort_index()
    fuel = eia.set_index("month").sort_index()
    panel = panel.join(fuel, how="outer").sort_index()

    next_month = panel.index.max() + pd.offsets.MonthBegin(1)
    panel = panel.reindex(panel.index.append(pd.DatetimeIndex([next_month])))

    # Monthly-average retail gasoline % change -> CPI gasoline NSA m/m (slope ~1,
    # corr 0.995). This is the high-frequency driver of the headline nowcast.
    panel["eia_gas_mm"] = panel["gas_price"].pct_change() * 100.0
    panel["wti_mm"] = panel["wti"].pct_change() * 100.0

    # COVID shock months: gasoline collapsed/rebounded violently; mask from error
    # stats as unforecastable outliers (same convention as the NFP harness).
    panel["is_covid"] = (panel.index >= "2020-03-01") & (panel.index <= "2021-06-01")

    panel.index.name = "month"
    return panel
