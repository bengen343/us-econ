# Core PCE m/m forecast

**Target:** the m/m % change of the PCE price index excluding food and
energy (BEA NIPA table T20804 monthly, series `DPCCRG`) — the Fed's
preferred inflation gauge — released with Personal Income and Outlays ~the
25th-31st of M+1 (08:30 ET; occasionally slipping to the 1st-2nd). Job
`bea-core-pce-forecast` runs daily at 10:30 MT, writing m/m % + index level
to `bea_pce.forecast_core_pce` (+`_current` view).

## The structure of the problem

Core PCE is largely *constructed* from source data published ~2 weeks before
the release: most components are deflated by month-M CPI items (out ~the
10th-13th of M+1), and the famous out-of-CPI components come from the
month-M PPI (out ~the 11th-16th) — airfares, healthcare, portfolio
management. The public frontier (Employ America's Core-Cast) replicates
BEA's full accounting from those inputs to ~2bp average error; banks publish
post-PPI "CPI→PCE translation" updates the same way. We regress on the
headline pieces instead of rebuilding the accounting.

## Winning model (`ccpi_air_v1`)

Walk-forward expanding-window OLS of Δlog(core PCE) on [1, month-M core CPI
Δlog, month-M PPI scheduled-passenger-air Δlog]. MIN_TRAIN=96.

Backtest (test 2012+, COVID 2020-03..2021-06 masked), errors in m/m pp:

| method | MAE 2012+ | RMSE 2012+ | RMSE 2017+ | dir%* |
|---|---|---|---|---|
| **ccpi_air** (production) | **0.052** | 0.072 | 0.085 | 80-84 |
| kitchen (10 regressors) | 0.053 | 0.069 | 0.080 | 75-81 |
| ccpi (core CPI alone) | 0.053 | 0.074 | 0.087 | 78-82 |
| ar1 | 0.076 | 0.101 | 0.116 | 67-72 |
| carry-forward (zero) | 0.197 | 0.233 | 0.278 | ~50 |

*direction = above/below-median calls (sign hits are meaningless — core PCE
m/m is almost always positive).

All CPI-based specs cluster at 0.069-0.075 RMSE; the denser ones (S&P
portfolio proxy, physicians+hospitals PPI, seasonal terms) buy ≤0.003 for
2-6 extra regressors and extra collector series. LightGBM lost. At ~5bp MAE
the two-regressor OLS sits between naive AR (~8bp) and the full accounting
translation (~2bp).

## Data flow

Production reads BigQuery only: `bea_pce.price_indexes` (the collector built
for this forecast — full T20804, vintage-stamped), `bls_cpi.cpi_series`
(core CPI SA), `bls_ppi.ppi_series` (PPI airline industry series
`PCU481111481111`, added to the collector for this forecast). The window
self-gates: between a PCE release and the next month's CPI/PPI prints the
target month's regressors are missing and the job idles.

## Caveats / revisit

- **PPI portfolio management — the other famous wedge component — is
  currently unbacktestable:** the 2022 NAICS recode (523920→523940) removed
  the old series from the BLS API and `PCU523940523940` only starts 2022.
  The S&P 500 proxy did not earn a slot. Revisit once the new series has
  ~8 years of history, or splice the old data from archived files.
- **PCE revises more than CPI**: every release revises prior months and
  annual benchmarks reshuffle history, so the latest-vintage backtest is
  more optimistic relative to first prints than for the CPI forecasts. Our
  vintage-stamped collector accrues first prints for proper scoring.
- The residual ~5bp vs Core-Cast's ~2bp is the full component accounting
  (hundreds of mappings, imputed financial services, scope weights). If the
  marginal 3bp matters, that's a rebuild-the-accounting project, not a
  regression tweak.
- bls_cpi/bls_ppi BigQuery history starts 2006 (collector lookback), so
  production training uses ~2007+ vs the harness's 2000+ — immaterial at
  MIN_TRAIN=96, noted for exactness.
