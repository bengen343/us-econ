# AAA next-day gasoline price forecast

**Target:** AAA national-average regular retail gasoline price ($/gal), h=1
daily. Job `aaa-gasoline-forecast` runs daily 08:00 MT (after the 06:00 AAA
scrape and 07:00 EIA/futures collections), writing a point forecast to
`aaa_gasoline.forecast_regular` and a Gaussian predictive distribution
(0.5c buckets) to `aaa_gasoline.forecast_regular_dist` (+`_current` views).

## How it works (`ecm_sym_rbob_v1`, blend `ecm_seas_mom_v1`)

Retail gasoline and RBOB futures are cointegrated; retail closes the gap to
the futures-implied equilibrium over days-to-weeks. The production model is a
**symmetric error-correction model**: long-run `retail = a + b·RBOB`
re-estimated on an expanding window, short-run RBOB distributed lags (0-3) +
retail momentum, weekly step scaled to daily (÷5). The v2 blend re-centers the
EC term on the per-calendar-month retail−RBOB wedge (~20c/gal seasonal swing)
and mixes **25% ECM step with 75% AAA day-over-day momentum** (AAA daily AR(1)
≈ +0.7); momentum requires a scrape gap ≤ 3 days, else pure ECM.

Because AAA history only began 2026-05, the authoritative backtest is the
**weekly EIA retail analog** (2000+, walk-forward from 2010): symmetric RBOB
ECM beat the random walk (Diebold-Mariano scored), the **asymmetric
"rockets-and-feathers" variant did not improve out-of-sample**, and WTI added
nothing beyond RBOB. On the first live month of daily AAA data the momentum
blend cut MAE ~20% vs pure ECM (~5.1c vs ~5.2c — tiny n, indicative only).

Forecast sigma comes from weekly out-of-sample residuals scaled by √5; the
distribution table publishes 4-sigma of 0.5c bands.

## Known behavior / revisit

- **ECM overshoots during decelerating declines** (verified live, 2026-06
  review): the EC term sees the level gap but not the speed of approach —
  hence the momentum blend for point calls. Revisit the blend weights (75/25)
  once a few months of daily history accrue; they were set on ~1 month.
- The per-month seasonal wedge uses a 12-month rolling window
  (min_periods=9) — tolerant of scrape gaps but slow to adapt; re-examine
  after the first full seasonal cycle of AAA data.
- Daily AAA backtest is too short for a Diebold-Mariano verdict against the
  weekly-fit model; rerun the harness on daily data once ~2 years exist.
- Data gotchas (see memory/collector notes): EIA refiner wholesale
  discontinued 2022-03; EIA NY RBOB spot series is empty; Yahoo RBOB ticker is
  `RB=F` and `range=max` silently downsamples to monthly.
