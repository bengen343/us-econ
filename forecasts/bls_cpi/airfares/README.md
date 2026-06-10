# CPI airline-fares forecast

**Target:** `CUSR0000SETG01` — CPI airline fares index, SA. Nowcast of the
next print (h=1, SA level + m/m %), forecast just before the mid-(M+1) CPI
release. The level is the primary output (growth rate chained onto the last
published index).

## Why this design

Airline fares are the most volatile CPI services component, and the m/m series
**mean-reverts** (negative autocorrelation — a spike month is usually partly
given back). The literature puts jet-fuel pass-through at **1-4 quarters**
(IATA; academic work on fares-vs-fuel), so contemporaneous fuel was expected
to matter less than for gasoline, and the BLS PPI for scheduled passenger air
transportation (a monthly producer-side fare measure, M-1 print published
before the CPI release) was the prime exogenous candidate.

Both expectations were half-right: fuel *does* help — but through WTI rather
than jet fuel, and on top of the AR structure — while the PPI turned out to
*parallel* the CPI rather than lead it.

## Bake-off (harness.py — 2010-01..2026-05, 177 origins, COVID-masked)

Walk-forward expanding-window, y = Δlog(SA index). Fuel series enter as
complete-month means (WTI daily from BigQuery; jet fuel weekly from FRED).
Levels in index points.

| method | mm_MAE | mm_RMSE | lvl_MAE | dir% | notes |
|---|--:|--:|--:|--:|---|
| **ar2_wti0 (winner)** | **1.35** | **2.02** | **3.77** | 71 | AR(2) + WTI lags 0-2 |
| ar2_wti1 | 1.36 | 2.05 | 3.82 | 69 | parsimonious runner-up |
| ar2 | 1.47 | 2.17 | 4.13 | 68 | own history only |
| wti_lags (no AR2) | 1.51 | 2.24 | 4.25 | 63 | |
| jet_lags | 1.55 | 2.26 | 4.34 | 63 | weekly jet fuel loses to daily WTI |
| ppi_ind (PCU481111481111) | 1.64 | 2.35 | 4.59 | 63 | parallels, doesn't lead |
| baseline: rw_mm | 1.83 | 2.60 | 5.17 | 66 | carry forward last m/m |
| baseline: zero | 1.75 | 2.67 | 4.87 | 0 | |

Ranking is stable on the 2017+ window (where the short-history PPI commodity
series allows an all-spec comparison). Seasonal terms add nothing (SA target).
Month-M WTI is PIT-clean by the same argument as the gasoline forecast: daily
spot, fully published before the release.

## Production (`production/`, model `ar2_wti_v1`)

OLS of Δlog(SA idx) on `[1, own lags 1-2, Δlog WTI complete-month means at
lags 0-2]`. Inputs: `bls_cpi.cpi_series` + `eia_petroleum.prices` (RWTC) —
both already collected. Job `bls-airfares-forecast`, daily days 1-15 05:00 MT,
writes `bls_cpi.forecast_airfares` (+`_current`), targets `airfares_cpi_level`
/ `airfares_cpi_mm`.

**Idle-by-design:** right after a CPI release, the next target month's WTI is
incomplete, so the job logs "inputs incomplete" and skips until the 1st of the
following month. Expected, not a bug.

## Live scoring & revisit

- **2026-05 print (backdated out-of-sample check):** forecast +1.7% m/m
  (304.3) vs actual +2.7% (307.3) — error −3.0 points, inside the backtest
  level-MAE band, in a fuel-surge month.
- The PPI airline series (industry + commodity) is *not* collected — it lost
  the bake-off. Revisit if BLS's average-pricing methodology changes again.
- Jet fuel proper (FRED `WJFUELUSGULF`) lost to WTI on data cadence (weekly vs
  daily), not economics. If a daily jet-fuel source ever lands in
  `eia_petroleum`, retest it as a drop-in for WTI.
- Mean-reversion + fuel leaves ~2pp RMSE on the table vs the series' ~2.7pp
  variance — fare-level data (e.g. high-frequency OTA/booking indexes) is the
  only input class that could plausibly tighten this further.
