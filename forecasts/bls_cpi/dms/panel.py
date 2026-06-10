"""Monthly panel for the Cleveland-Fed-style bottom-up CPI reconstruction.

One row per month M (first-of-month Timestamp), extended one month past the last
CPI release so the harness can emit a live nowcast. Carries the published
actuals (targets + components, SA and NSA) and the high-frequency gasoline
signal; the deterministic nowcasts themselves are computed in ``harness``.

PIT timing model
----------------
We nowcast month M for its release ~mid-M+1. By that origin month M has fully
elapsed, so the entire month's EIA gasoline/oil prints are knowable, month M's
full Manheim wholesale used-vehicle index is published (5th business day of
M+1), and CPI component prints through M-1 are published. Everything used here
respects that.
"""

from __future__ import annotations

import pandas as pd


def build_panel(
    cpi: pd.DataFrame, eia: pd.DataFrame, manheim: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Join CPI (wide, latest vintage) with monthly fuel prices and the Manheim
    used-vehicle index; add the gasoline/Manheim monthly changes and a COVID
    mask; extend one month for the live nowcast."""
    panel = cpi.set_index("month").sort_index()
    fuel = eia.set_index("month").sort_index()
    panel = panel.join(fuel, how="outer").sort_index()
    if manheim is not None and not manheim.empty:
        panel = panel.join(manheim.set_index("month").sort_index(), how="outer").sort_index()

    next_month = panel.index.max() + pd.offsets.MonthBegin(1)
    panel = panel.reindex(panel.index.append(pd.DatetimeIndex([next_month])))

    # Monthly-average retail gasoline % change -> CPI gasoline NSA m/m (slope ~1,
    # corr 0.995). This is the high-frequency driver of the headline nowcast.
    panel["eia_gas_mm"] = panel["gas_price"].pct_change() * 100.0
    panel["wti_mm"] = panel["wti"].pct_change() * 100.0

    # Wholesale used-vehicle (Manheim SA) % change -> leads CPI used cars by
    # ~1-2 months; the used-cars equation in `harness` regresses on it.
    if "manheim_sa" in panel:
        panel["manheim_mm"] = panel["manheim_sa"].pct_change() * 100.0
    else:
        panel["manheim_mm"] = float("nan")

    # COVID shock months: gasoline collapsed/rebounded violently; mask from error
    # stats as unforecastable outliers (same convention as the NFP harness).
    panel["is_covid"] = (panel.index >= "2020-03-01") & (panel.index <= "2021-06-01")

    panel.index.name = "month"
    return panel
