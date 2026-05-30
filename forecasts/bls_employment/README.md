# BLS Employment Situation forecasts

Two **separate, offline research harnesses** that forecast the monthly
Employment Situation release, sharing one read-only data layer (`data.py`):

| Target | Package | Definition |
|---|---|---|
| Nonfarm payrolls headline | `payrolls_headline/` | MoM change in `CES0000000001` (SA total nonfarm, thousands) |
| Unemployment rate | `unemployment_rate/` | `LNS14000000` level (percent) — modelled in both level and change framings |

Both forecast month **M** at its release origin (first Friday of **M+1**), using
only information knowable then, and run an expanding-window walk-forward backtest
against naive baselines. Nothing writes BigQuery — these are research harnesses,
to be productionized later like the claims / ADP forecasts once a method proves
out.

```
.\.venv\Scripts\python.exe -m forecasts.bls_employment.payrolls_headline.harness
.\.venv\Scripts\python.exe -m forecasts.bls_employment.unemployment_rate.harness
.\.venv\Scripts\python.exe -m forecasts.bls_employment.timesfm_bench   # TimesFM (AI.FORECAST) benchmark, both targets
```

```
.\.venv\Scripts\python.exe -m forecasts.bls_employment.bqml_bench       # BOOSTED_TREE + ARIMA_PLUS_XREG
```

`timesfm_bench.py` (TimesFM via `AI.FORECAST`, temp tables only) and
`bqml_bench.py` (BOOSTED_TREE_REGRESSOR + ARIMA_PLUS_XREG, throwaway models/tables
in the `bls_employment` dataset, dropped after) survey the BigQuery-native ML
options. **Verdict: none beat the linear RidgeCV harness on either target** —
TimesFM (univariate) can't see the weekly-claims/IUR signal, BOOSTED_TREE
overfits at n≈150 monthly rows, and ARIMA_PLUS_XREG is unstable (≈ random walk or
worse). See each package README for the full leaderboard.

## Shared data layer (`data.py`)

Latest-vintage-per-period, read-only pulls:
- `pull_bls_series` — Employment Situation series (targets + components).
- `pull_claims_national` — national SA initial/continued claims **and IUR**
  (insured unemployment rate — a direct weekly coincident analogue of the UR).
- `pull_adp_monthly` / `pull_adp_pulse` — ADP monthly headline (released ~2 days
  before NFP) and the weekly Pulse.
- `pull_challenger` — monthly announced job cuts / hiring plans.
- `pull_trends` — weekly Google Trends search interest.

### Load-bearing caveats
- **Only 2 BLS vintages exist** (2026-05-06 backfill + 2026-05-29 year-cap fix),
  so there is no real first-print PIT history. We backtest the **revised** level
  as a proxy for the first print. NFP first-print revisions are large, so
  backtest error is **optimistic** vs the true as-reported objective. One true
  first-print accrues per monthly release going forward.
- **COVID months (2020-03 .. 2021-06) are masked** from both training and the
  test window — unforecastable outliers (Apr-2020 NFP −20,787k; UR 14.8%).
- Feature coverage differs: ADP from 2010, Trends from 2021, ADP Pulse from
  2026 (~16 weeks), **Challenger layoffs only ~15 months**. Specs that use short
  series are evaluated only on the origins where their features exist (small-n,
  flagged in each scorecard).

See each package's `README.md` for the empirical leaderboards and verdicts.
