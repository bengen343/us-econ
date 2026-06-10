# Headline PPI final demand y/y forecast

**Target:** the y/y % change of PPI Final Demand (`WPUFD4`, NSA — the BLS
headline convention), for the next-to-be-released month M, forecast through
the pre-release window (PPI for M releases ~the 11th-16th of M+1, 08:30 ET).
The m/m % and NSA index level are published alongside it. Job
`bls-ppi-headline-forecast` runs daily on days 1-15 at 05:00 MT (gated in code
to day ≤ 18), writing `bls_ppi.forecast_headline` (+`_current` view).

## The structure of the problem

Only the month-M m/m is unknown at the origin: the 12-month base is published,
so `yy_M = I_{M-1}·exp(Δlog_M)/I_{M-12} − 1`. Every candidate forecasts
Δlog(NSA index) and the y/y follows arithmetically. Two release-calendar facts
do most of the work:

1. **PPI prices reference ONE day** — the Tuesday of the week containing the
   13th (lands the 9th-15th) — unlike CPI's full-month average. So energy
   regressors are sampled *at that pricing date* (`monthly_at_pricing_date`
   in data.py), and month M's values are fully observed weeks before the
   release.
2. **ISM prices paid for month M is released the 1st business day of M+1**,
   before the PPI print — the month-M survey is usable at lag 0. The Cleveland
   Fed (EC 2018-05) found the ISM mfg price index has predictive content for
   PPI specifically (corr 0.43 one month ahead) but not for CPI/PCE.

## Winning model (`gas_dsl_ism_v1`)

Walk-forward expanding-window OLS of Δlog(`WPUFD4`) on [1, Δlog gasoline Gulf
Coast spot at the pricing date, Δlog retail diesel at the pricing date,
(ISM mfg prices − 50)/100 for month M, expanding calendar-month seasonal mean,
SA headline m/m lag 1]. MIN_TRAIN=72 (FD-ID history only begins 2009-11).

Backtest (latest vintage, COVID 2020-03..2021-06 masked), errors in y/y pp:

| method | yy_RMSE 2017+ | yy_RMSE 2022+ | dir% 2017+ |
|---|---|---|---|
| **gas_dsl_ism** (production) | **0.29** | **0.32** | **86.2** |
| + Henry Hub / + trade-svc lag (kitchen) | 0.29 | 0.32-0.33 | 86.2 |
| gas_diesel (no ISM) | 0.30 | 0.34 | 81.9 |
| gas_ism (no diesel) | 0.31 | 0.36 | 81.9 |
| gas_mid only | 0.34 | 0.38 | 79.8 |
| lgbm (11-feature ML challenger) | 0.34 | 0.38 | 83.0 |
| gas_avg (complete-month mean instead of pricing date) | 0.35 | 0.39 | 77.7 |
| ism_mfg only | 0.36 | 0.42 | 80.9 |
| comp (FD-ID component lags) | 0.40 | 0.48 | 78.7 |
| imports lag 1 | 0.40 | 0.47 | 77.7 |
| ar2_sa (pure own history) | 0.40 | 0.47 | 78.7 |
| rw_mm | 0.56 | 0.63 | 73.4 |
| **rw_yy (carry y/y forward)** | **0.58** | **0.71** | 38.3 |

−50% RMSE vs the y/y random walk on both windows; direction is the sign of
the y/y *change* vs the prior print. The pricing-date sampling beats the
complete-month mean (0.34 vs 0.35 for gas alone, and consistently in combos) —
the measurement-date argument, confirmed. Henry Hub, trade-services margins
(lag 1), import prices, extra gas lags, and LightGBM all add nothing; the
losers that would have required new collectors (Henry Hub via EIA natural-gas
API) lost, so **production reads only already-collected tables**:
`bls_ppi.ppi_series`, `eia_petroleum.prices`, `ism.report_on_business`.

Timing behavior: right after a PPI release the target rolls to the next month
and the job idles (returns no rows) until that month's ISM print lands on the
1st business day — expected, not a bug.

## Performance / live

- First live nowcast (made 2026-06-10, the day before the release): May 2026
  **+6.6% y/y** (+0.9% m/m, index 158.258), n_train=185. The BigQuery
  production path and the BLS-API harness path produced identical numbers.
- Test-window bias is ≈ −0.05pp (slight underprediction during the 2025-26
  producer-price surge).

## Caveats / revisit

- **Backtests are against the latest vintage.** PPI revises for four months
  after first print (iterative since Nov 2021); first-print accuracy will
  look slightly worse. Our `ppi_series` table is append-only/vintage-stamped,
  so a first-print-vs-final scoring becomes possible as vintages accrue.
- **Short history:** FD-ID starts 2009-11, so MIN_TRAIN=72 (vs the repo's
  usual 96) and the test window opens 2017. Re-examine once a few more years
  accrue; the legacy finished-goods series (`WPUFD49207`, 1940s+) is collected
  if a long-history bridge is ever wanted.
- **Trade-services margins are the irreducible noise** (~20% of FD weight,
  margin = selling − acquisition price). Nothing tested predicts them at h=1;
  a m/m miss of 0.2-0.3pp from a margin swing is within model behavior.
- The ISM dependency means a stale `ism` collector silently degrades the
  forecast to None (the job idles). The 2026-06 forecast review caught
  exactly this failure mode on other survey collectors — worth a freshness
  check if the job idles past the 5th.
- S&P Global flash PMI (prices) would give a mid-month-M read before ISM,
  but our collection only has `business_activity` for services — a possible
  future input if the collector is extended.
