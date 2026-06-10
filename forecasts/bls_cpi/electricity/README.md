# Electricity average-price forecast

**Target:** `APU000072610` — BLS average price of electricity ($/kWh, NSA; the
FRED per-kWh series). Nowcast of the next print (h=1, level + m/m %), forecast
just before the mid-(M+1) CPI release.

## Why this design

Retail electricity is an **administered price**: rates move through utility
rate cases, so wholesale and fuel costs pass through with months-to-years
delays (EIA; RFF), and retail prices have structurally diverged from natural
gas since ~2023 (grid/transmission costs — FRED Blog). The NSA series also has
strong calendar seasonality from summer rate schedules (June alone averages
+4-5% m/m). Prior: persistence + seasonality should dominate, with
producer-side (PPI electric power) and fuel (Henry Hub) inputs having to earn
their place. They didn't.

## Bake-off (harness.py — 2010-01..2026-05, 178 origins, COVID-masked)

Walk-forward expanding-window, y = Δlog(AP). Inputs tested: PPI electric power
(residential `WPU0541`, all-sector `WPU054`; lags 1-3 + trailing) and Henry Hub
(FRED `MHHNGSP`, keyless CSV; lags 0-12 + trailing). Levels in cents/kWh.

| method | mm_MAE | mm_RMSE | lvl_MAE | notes |
|---|--:|--:|--:|---|
| **seasonal_ar12 (winner)** | **0.72** | **0.95** | **0.105** | own lags 1, 12 + seasonal mean |
| ppi_res_trail | 0.72 | 0.98 | 0.106 | best exogenous spec — still loses |
| hh_trail | 0.73 | 0.99 | 0.107 | trailing gas: no gain |
| hh_now | 0.75 | 1.02 | 0.111 | contemporaneous gas: no gain |
| seasonal only | 0.85 | 1.18 | 0.125 | |
| baseline: ar1 | 1.15 | 1.60 | 0.167 | |
| baseline: rw | 1.13 | 1.66 | 0.163 | |

Pure own-history wins — every PPI and Henry Hub variant scored worse. −43%
RMSE vs random walk comes almost entirely from the seasonal structure plus
short- and year-lag persistence.

## Production (`production/`, model `seasonal_ar12_v1`)

OLS of Δlog(AP) on `[1, Δlog AP_{M-1}, Δlog AP_{M-12}, expanding
calendar-month mean]`. Input: `bls_cpi.average_prices` **only** — no collector
or dependency changes were needed. Job `bls-electricity-forecast`, daily days
1-15 05:00 MT, writes `bls_cpi.forecast_electricity` (+`_current`), targets
`electricity_ap_level` / `electricity_ap_mm`.

## Live scoring & revisit

- **2026-05 print (first live test): exact.** Forecast $0.196 (+0.9% m/m)
  computed minutes before BigQuery ingested the release; actual $0.196
  (+1.0%). (The DRY_RUN happened to straddle the release — a clean accidental
  out-of-sample test.)
- The current call for **2026-06 is $0.204 (+4.3%)** — the summer rate-schedule
  jump; June is the model's highest-variance month, so this print is the most
  informative one to score.
- Revisit if the 2026 grid-cost surge steepens: EIA's STEO projects +13-18%
  residential prices through 2026, a regime where the expanding seasonal mean
  (which averages over calm decades) could lag. A recency-weighted seasonal
  (e.g. 10-year window) is the cheap variant to test first.
- PPI electric power (`WPU0541`) is *not* collected — it lost the bake-off, so
  it was left out of the bls_ppi collector. If it's ever wanted, it's a
  one-line `COMMODITY_ITEMS` addition.
