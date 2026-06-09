r"""Walk-forward research harness: next-day AAA retail gasoline forecast.

The live target is the daily AAA national-average regular price, but it only began
in 2026-05 (~1 month), far too short to fit + backtest an ECM with rolling
Diebold-Mariano tests. So methodology is validated on its long-history analog --
EIA weekly U.S. regular retail gasoline (2000-present, the same quantity sampled
weekly) -- and the chosen model is then applied to produce a LIVE next-day AAA
forecast, anchored to the latest AAA level (the hybrid approach).

Two parts:

  1. RESEARCH (weekly h=1, EIA retail): expanding-window walk-forward comparing a
     random-walk baseline against AR(1), a symmetric ECM on RBOB, an asymmetric
     ("rockets and feathers") ECM, and a symmetric ECM augmented with WTI. Scored
     by MAE/RMSE on the price level and Diebold-Mariano vs the random walk.

  2. LIVE (daily h=1, AAA): the asymmetric ECM is fit on the full weekly history;
     its long-run (a, b) gives the RBOB-implied equilibrium for today's RBOB, and
     the short-run coefficients give the expected weekly move, ~1/5 of which is
     the next-day change applied to the latest AAA level. A thin daily backtest on
     the available AAA history is reported, heavily caveated by its tiny n.

Read-only. Run: .\.venv\Scripts\python.exe -m forecasts.aaa_gasoline.next_day.harness
"""

from __future__ import annotations

import pandas as pd

from forecasts.aaa_gasoline.next_day import data, model

TEST_START = pd.Timestamp("2008-01-01")
TRADING_DAYS_PER_WEEK = 5.0
# The research winner shipped to production: symmetric RBOB ECM (asymmetry did not
# help out-of-sample; WTI added nothing over RBOB).
PRODUCTION_SPEC = "ecm_sym"


# --------------------------------------------------------------------------- #
# Panels
# --------------------------------------------------------------------------- #
def build_weekly_panel(eia_retail: pd.Series, futures: pd.DataFrame) -> pd.DataFrame:
    """Weekly (Monday-dated) panel: retail (EIA regular) + rbob (RB=F weekly mean)
    + wti (CL=F weekly mean), aligned on the EIA Monday grid, rows complete."""
    rbob_w = futures["rbob"].resample("W-MON").mean()
    wti_w = futures["wti"].resample("W-MON").mean()
    panel = pd.concat(
        [eia_retail.rename("retail"), rbob_w.rename("rbob"), wti_w.rename("wti")],
        axis=1,
    ).dropna()
    return panel.sort_index()


# --------------------------------------------------------------------------- #
# Research (weekly)
# --------------------------------------------------------------------------- #
def _fmt(name: str, s: dict[str, float], dm: dict | None) -> str:
    line = (
        f"  {name:<42} n={s['n']:>4.0f}  MAE={s['MAE'] * 100:5.2f}c  "
        f"RMSE={s['RMSE'] * 100:5.2f}c  bias={s['bias'] * 100:+5.2f}c"
    )
    if dm is not None:
        if dm["dm"] != dm["dm"]:  # NaN
            line += "   DM vs RW: n/a"
        else:
            verdict = "better" if dm["dm"] < 0 else "worse"
            sig = "*" if dm["p"] < 0.05 else " "
            line += f"   DM vs RW: {dm['dm']:+5.2f} (p={dm['p']:.3f}){sig} {verdict}"
    return line


def run_research(panel: pd.DataFrame) -> None:
    actual = panel["retail"]
    rw = model.random_walk(panel, TEST_START)
    results: list[tuple[str, dict, dict | None, float]] = []

    rw_score = model.score(actual, rw)
    results.append(("Random walk (r_hat = r_t)", rw_score, None, rw_score["RMSE"]))

    for spec in model.SPECS:
        pred = model.walk_forward(panel, spec, TEST_START)
        s = model.score(actual, pred)
        dm = model.dm_test(actual, pred, rw)
        results.append((spec.label, s, dm, s["RMSE"]))

    test = actual[actual.index >= TEST_START].dropna()
    print("=" * 100)
    print("RESEARCH: weekly h=1, EIA U.S. regular retail gasoline (AAA long-history analog)")
    print(
        f"Panel {panel.index.min().date()}..{panel.index.max().date()} "
        f"({len(panel)} wks); test {test.index.min().date()}..{test.index.max().date()} "
        f"({len(test)} wks). Errors in cents/gal; DM<0 & p<0.05* beats RW."
    )
    print("=" * 100)
    for name, s, dm, _ in sorted(results, key=lambda kv: kv[3]):
        print(_fmt(name, s, dm))
    print()


# --------------------------------------------------------------------------- #
# Live daily AAA forecast
# --------------------------------------------------------------------------- #
def run_live(panel: pd.DataFrame, aaa: pd.Series, futures: pd.DataFrame) -> None:
    spec = next(s for s in model.SPECS if s.name == PRODUCTION_SPEC)
    rbob_daily = futures["rbob"].dropna()
    rbob0 = float(rbob_daily.iloc[-1])
    rbob0_date = rbob_daily.index[-1].date()
    aaa0 = float(aaa.iloc[-1])
    aaa0_date = aaa.index[-1].date()

    nd = model.next_day_forecast(panel, spec, aaa0, rbob0, TRADING_DAYS_PER_WEEK)

    print("=" * 100)
    print(f"LIVE: next-day AAA national-average regular forecast ({spec.label})")
    print("=" * 100)
    print(f"  Long-run: AAA* = {nd.a:.3f} + {nd.b:.3f}*RBOB  (wedge {nd.a * 100:.0f}c)")
    print(f"  Latest AAA regular:       {aaa0:.3f} $/gal   ({aaa0_date})")
    print(f"  Latest RBOB (RB=F):       {rbob0:.3f} $/gal   ({rbob0_date})")
    print(f"  RBOB-implied equilibrium: {nd.equilibrium:.3f} $/gal")
    gap = -nd.ec
    pull = "UP" if gap > 0 else "DOWN"
    print(
        f"  Disequilibrium gap:       {gap * 100:+.1f}c  "
        f"(AAA is {'below' if gap > 0 else 'above'} equilibrium -> pent-up pull {pull})"
    )
    print(f"  Expected weekly move:     {nd.weekly_move * 100:+.2f}c/gal")
    print(f"  ==> NEXT-DAY forecast: {nd.next_day:.3f} $/gal  ({nd.next_day - aaa0:+.3f} vs today)")
    print()


def run_live_backtest(panel: pd.DataFrame, aaa: pd.Series, futures: pd.DataFrame) -> None:
    """Thin daily backtest on the available AAA history: production ECM vs RW.

    Tiny n (AAA history is ~1 month), so this is indicative only -- it cannot
    support a Diebold-Mariano verdict. Reported for transparency / smoke-testing.
    The weekly lag terms barely move across this ~1-month window, so the EC term
    (each day's disequilibrium vs RBOB) carries the daily signal.
    """
    spec = next(s for s in model.SPECS if s.name == PRODUCTION_SPEC)
    rbob_daily = futures["rbob"].reindex(futures["rbob"].index.union(aaa.index)).ffill()
    aaa_sorted = aaa.sort_index()
    rows = []
    for i in range(len(aaa_sorted) - 1):
        d_today = aaa_sorted.index[i]
        r_today, r_next = aaa_sorted.iloc[i], aaa_sorted.iloc[i + 1]
        if d_today not in rbob_daily.index:
            continue
        nd = model.next_day_forecast(
            panel, spec, r_today, float(rbob_daily.loc[d_today]), TRADING_DAYS_PER_WEEK
        )
        rows.append({"actual": r_next, "ecm": nd.next_day, "rw": r_today})
    if not rows:
        return
    bt = pd.DataFrame(rows)
    ecm = model.score(bt["actual"], bt["ecm"])
    rw = model.score(bt["actual"], bt["rw"])
    print("=" * 100)
    print(f"LIVE BACKTEST (daily AAA, n={len(bt)} day-pairs -- INDICATIVE ONLY, too short for DM)")
    print("=" * 100)
    print(f"  Random walk      MAE={rw['MAE'] * 100:5.2f}c  RMSE={rw['RMSE'] * 100:5.2f}c")
    print(f"  Production ECM   MAE={ecm['MAE'] * 100:5.2f}c  RMSE={ecm['RMSE'] * 100:5.2f}c")
    print()


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #
def run() -> None:
    print("Pulling BigQuery inputs (read-only)...\n")
    c = data._client()
    eia_retail = data.pull_eia_retail_weekly(c)
    futures = data.pull_futures_daily(c)
    aaa = data.pull_aaa_regular(c)

    panel = build_weekly_panel(eia_retail, futures)
    run_research(panel)
    run_live(panel, aaa, futures)
    run_live_backtest(panel, aaa, futures)


if __name__ == "__main__":
    run()
