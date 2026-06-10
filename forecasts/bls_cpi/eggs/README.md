# Egg average-price forecast

**Target:** `APU0000708111` — BLS average price of eggs, grade A, large ($/dozen,
NSA; the famous FRED series). Nowcast of the next print (h=1), forecast just
before the mid-(M+1) CPI release. Published as both a level and m/m %.

## Why this design

Literature review (2026-06): retail egg prices follow **wholesale with a 2-5
week lag** (USDA ERS, farmdoc); HPAI layer culls are the structural shock but
transmit *through* wholesale; ARIMAX beat LSTM in the closest published
comparison (Q Open 2025), and USDA's own Food Price Outlook is pure
time-series — so the candidate set was pass-through regressions vs time-series
vs light ML, not deep learning.

**Timing is the key fact:** the PPI print for M-1 lands mid-M, *before* the
CPI/AP print for M (~10th-15th of M+1). So PPI chicken eggs (`WPU017107`,
monthly from 1991-12) at lags 1-3 is PIT-clean and sits exactly in the
documented pass-through window. Daily USDA AMS wholesale would be even better
(month-M coverage) but needs a MARS API key — see "Revisit" below.

## Bake-off (harness.py — 2010-01..2026-04, 177 origins, COVID-masked)

Walk-forward expanding-window, y = Δlog(AP). m/m errors in pct, levels $/dozen.

| method | mm_MAE | mm_RMSE | lvl_MAE | dir% | notes |
|---|--:|--:|--:|--:|---|
| **ppi_dl3_ar_seas (winner)** | **5.11** | **6.85** | **0.125** | 66 | AR(1) + PPI lags 1-3 + seasonal |
| ppi_dl3_ar | 5.21 | 6.95 | 0.129 | 66 | drop the seasonal term |
| ecm_dl3 | 5.21 | 6.96 | 0.130 | 66 | + retail/wholesale gap: no gain |
| ecm_asym | 5.38 | 7.25 | 0.134 | 66 | rockets-and-feathers terms: no gain |
| lgbm | 5.51 | 7.51 | 0.137 | 67 | overfits at n≈400 monthly rows |
| sarima | 5.93 | 8.07 | 0.150 | 58 | (1,1,1)(0,1,1,12) on log level |
| baseline: rw | 6.00 | 8.32 | 0.150 | 2 | |
| baseline: ar1 | 6.13 | 8.43 | 0.153 | 43 | |

The winner also leads the HPAI-era subwindow (2015+: RMSE 7.87 vs 7.90 next).
ECM gap and asymmetric terms added nothing once three wholesale lags were in.

## Production (`production/`, model `ppi_dl3_seas_v1`)

OLS of Δlog(AP) on `[1, Δlog AP_{M-1}, Δlog PPI_{M-1..M-3}, expanding
calendar-month mean]`, fit on all published history each run. Inputs:
`bls_cpi.average_prices` + `bls_ppi.ppi_series` (WPU017107 added to the
collector for this). Job `bls-eggs-forecast`, daily days 1-15 05:00 MT, writes
`bls_cpi.forecast_eggs` (+`_current` view), targets `eggs_ap_level` /
`eggs_ap_mm`.

## Live scoring & revisit

- **2026-05 print (first live test): MISS on magnitude.** Forecast $2.02
  (−10.4% m/m) on the April wholesale collapse (PPI 172→88); actual $2.19
  (−2.6%). Retail stayed sticky on the way down — the classic "feathers" half
  of asymmetric pass-through. The asymmetric spec lost the backtest *on
  average*, but this episode is exactly where it differs; revisit once a few
  more sharp wholesale declines accrue.
- **USDA AMS daily wholesale** (MARS API, free key required): would give
  month-M wholesale in real time instead of lag-1. The single most promising
  upgrade; the daily national index history is short (~2023+), so blend with
  PPI rather than replace.
- Backtests use the latest vintage (AP/PPI revisions are minor) and train from
  1991 via the BLS API; production trains on BigQuery's 2006+ window —
  coefficients differ slightly (validated equivalent in `_validate_eggs_prod.py`-style checks).
