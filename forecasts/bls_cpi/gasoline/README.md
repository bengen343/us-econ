# CPI gasoline forecast

**Target:** `CUSR0000SETB01` — CPI gasoline (all types) index, SA. Nowcast of
the next print (h=1, SA level + m/m %), forecast just before the mid-(M+1)
CPI release.

## Why this design

The Cleveland Fed's inflation nowcast (Knotek-Zaman — the framework our
headline `dms` forecast replicates) maps weekly EIA retail gasoline onto the
CPI gasoline component, and most of its documented accuracy edge comes from
this energy channel. BLS computes the index from **daily pump prices over the
full calendar month** — so by our origin (just before the release), the target
month's retail prices are *fully published*. That kills the need for
oil/futures inputs entirely: Cleveland Fed needs them only to extrapolate the
unfinished month. The regressor is the month's own complete retail change,
**contemporaneous**, not lagged.

Inputs: EIA weekly all-grades retail (`EMM_EPM0_PTE_NUS_DPG`, 2000+, BigQuery)
aggregated to **complete-month means** (a month counts only when its weekly
obs span it end-to-end — the production guard against forecasting from a
half-published month). AAA daily would mirror BLS's sampling even better but
has only ~1 month of history (collected from 2026-05) — see "Revisit".

## Bake-off (harness.py — 2010-01..2026-05, 180 origins, COVID-masked)

Walk-forward expanding-window, y = Δlog(SA index). Levels in index points.

| method | mm_MAE | mm_RMSE | lvl_MAE | dir% | notes |
|---|--:|--:|--:|--:|---|
| **eia_wedge (winner)** | **1.28** | **1.55** | **3.47** | 87 | retail change + calendar wedge |
| dms_determ | 1.30 | 1.56 | 3.53 | 87 | deterministic, coefficient 1 imposed |
| eia_dl_ar_wedge | 1.31 | 1.59 | 3.53 | 87 | + AR/lag terms: slightly worse |
| eia (no wedge) | 1.86 | 2.29 | 5.03 | 77 | seasonality matters ~0.7pp RMSE |
| baseline: ar1 | 3.13 | 4.40 | 8.44 | 60 | |
| baseline: rw | 3.23 | 4.46 | 8.69 | 0 | |

The `wedge` is the expanding calendar-month mean of (target − retail change) —
it learns the BLS seasonal factor. Notably the deterministic dms form already
used inside the headline CPI forecast is statistically tied with the fitted
winner — good independent validation of that design.

## Production (`production/`, model `eia_wedge_v1`)

OLS of Δlog(SA idx) on `[1, eia_mm, wedge]`. Inputs: `bls_cpi.cpi_series` +
`eia_petroleum.prices`. Job `bls-gasoline-forecast` (distinct from
`aaa-gasoline-forecast`, the next-day retail price job), daily days 1-15
05:00 MT, writes `bls_cpi.forecast_gasoline` (+`_current`), targets
`gas_cpi_level` / `gas_cpi_mm`.

## Live scoring & revisit

- **2026-05 print (first live test): within band.** Forecast 374.8 (+5.9% m/m
  SA) pre-release; actual 378.7 (+7.0%). Error −3.9 points vs backtest level
  RMSE 4.3 — May was an accelerating leg of the 2026 fuel surge (retail $2.94
  Jan → $4.61 May), where a monthly-mean regressor slightly trails.
- **AAA daily** (`aaa_gasoline.daily`): once ~2 years accrue, test replacing
  the weekly-mean regressor with the daily calendar-month mean — closer to
  BLS's own sampling frame, and it would also catch intra-week turns the
  Monday-only EIA series misses.
- SA backtest caveat (shared with the headline harness): the SA series is
  re-seasonalised annually, so backtest errors are mildly optimistic vs true
  first prints.
