# Housing starts + building permits forecasts

**Targets:** total housing starts and total building permits (SAAR,
thousands) for the next month of the joint Census/HUD New Residential
Construction release (~the 16th-19th of M+1, 08:30 ET). Starts and permits
publish *together*, so at the origin both are known only through M-1. Job
`census-starts-permits-forecast` runs daily at 10:00 MT on days 1-20
(after the 09:00 collector runs), writing SAAR level + m/m % per target to
`census_construction.forecast_starts_permits` (+`_current` view).

## Winning models

Walk-forward expanding-window OLS on Δlog SAAR, MIN_TRAIN=120 (joint history
starts 1985 with the HMI):

- **Starts (`ecm_hmi_wx_v1`)**: lagged log(permits/starts) gap — an ECM,
  starts converge to the permitted pipeline — plus the own lag, the month-M
  NAHB HMI change, and NOAA temperature deviations from calendar-month norms
  (months M and M-1). Timing facts that make this PIT-clean: HMI for M is
  released ~the 16th of M (a month before the M starts print; Fed-validated
  predictor, Goodman 1994), and NOAA posts month-M temperature ~the 8th of
  M+1, a week before the release.
- **Permits (`sf_mf_split_v1`)**: single-family and 5+ permit changes at lag
  1 as separate regressors. The split beats the aggregate AR because the two
  segments have different dynamics (~half of SF homes start the month their
  permit issues; 5+ is lumpy with a long pipeline) — and it needs no inputs
  beyond the Census data itself.

Backtest (2010+, COVID 2020-03..2021-06 masked), m/m % and SAAR thousands:

| target | spec | RMSE 2010+ | RMSE 2017+ | lvl MAE | dir% |
|---|---|---|---|---|---|
| starts | **ecm_hmi_wx** | **6.27** | **5.42** | 57k | 76 |
| starts | kitchen (+mortgage, +permits lag) | 6.15 | 5.43 | 55k | 77 |
| starts | SF/MF bottom-up recomposition | 6.20 | 5.75 | 57k | 78 |
| starts | ar1 | 7.34 | 6.09 | 63k | 69 |
| starts | carry-forward | 8.24 | 6.89 | 73k | — |
| permits | **sf_mf** | **4.63** | **4.25** | 43k | 65 |
| permits | ar_hmi | 4.82 | 4.29 | 44k | 63 |
| permits | ar1 | 4.93 | 4.50 | 46k | 61 |
| permits | carry-forward | 5.03 | 4.63 | 47k | — |

Losers: mortgage rates (PMMS — tied at best, would have cost a fourth
collector), pure-HMI / pure-weather / permits-bridge starts specs, the
SF/MF bottom-up for starts (competitive pre-2017 only; note the published
2-4-unit SA column is mostly suppressed "(S)" — any recomposition must use
the total−SF−5+ residual), LightGBM on both targets.

First live calls (made 2026-06-10 for the ~06-17 release): **May 2026
starts 1445k SAAR (−1.4% m/m), permits 1396k (−1.9%)**.

## Data flow

Production reads BigQuery only — the three collectors built for this
forecast: `census_construction.new_residential_construction` (official
census.gov history workbooks; the EITS API now requires a key),
`nahb_hmi.housing_market_index`, `noaa_climate.climate_at_a_glance`. All are
append-only/vintage-stamped; the forecast queries dedupe to the latest
vintage. The harness pulls the same sources (plus Freddie Mac PMMS for the
losing mortgage specs) from the public files directly.

Within each month the two targets complete at different times: permits is
computable right after the prior release (its regressors are M-1 Census
data); starts waits for the month-M temperature (~the 9th). The job loads
whichever is ready — permits-only rows late in the month, both from ~the 9th
through the release.

## Caveats / revisit

- **Backtests are against the latest vintage.** Starts/permits revise for
  two months after first print (average ≤2.9%); first-print accuracy will
  look modestly worse. Our collector preserves vintages, so first-print
  scoring accrues from 2026-06 onward.
- **ECM over-prediction bias (+1.6-1.9 m/m pp on the test windows):** the
  permits-over-starts gap has persisted since 2022 without fully converging
  (affordability, builder uncertainty). An intercept recentering or a
  time-varying gap coefficient is the first revisit once more post-2022
  data accrues.
- Starts m/m is inherently noisy (90% CI on the published m/m change is
  ~±10pp): a 57k level MAE on a ~1,400k base is honest performance, not a
  precision instrument.
- HMI components (future sales, traffic) are collected but untested —
  candidate refinement. Same for NOAA precipitation (schema supports it).
- The 2026-06 DRY_RUN against BigQuery awaits the collectors' first
  scheduled runs (NOAA ~the 11th, NAHB ~the 15th, Census ~the 16th); the
  compute path was validated with file-sourced inputs in the meantime.
