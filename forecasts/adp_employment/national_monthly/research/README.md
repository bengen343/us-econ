# ADP national-monthly headline — forecast research harness

Offline, **read-only** harness to develop and honestly score a nowcast of the
ADP National Employment Report monthly headline (private SA employment change).

```
.\.venv\Scripts\python.exe -m forecasts.adp_employment.national_monthly.research.harness
```

## Target

`headline(M) = ner_sa(M) − ner_sa(M−1)` for `timestep='M', aggregation='National',
category='U.S.'` in `adp_employment.ner_history`. Verified to reproduce ADP's
published figures (Apr-2026 = +109k, Mar-2026 = +61k).

We want the **first-print** headline (as reported on release day, first Wednesday
of M+1). The collector has captured only **one vintage so far (2026-05-06)**, so
there is no point-in-time first-print history yet. The harness therefore scores
against the **fully-revised** SA level as a *proxy*, and true first-print history
accrues one month per release going forward.

## PIT timing model

Forecast month M just before its release (first Wednesday of M+1). Knowable then:

| Signal | Availability at origin | Use |
|---|---|---|
| Monthly ADP headlines | through **M−1** | momentum / AR |
| Weekly NER Pulse (SA 4wk-MA change) | weeks ending in **M** | within-month nowcast |
| Initial claims (SA) | weeks ending in **M** | coincident |
| Google Trends | weeks ending in **M** | coincident/leading |
| Weekly ADP NSA level (`timestep='W'`) | lags ~7 wks | omitted |

Weekly series are averaged over the weeks **ending in calendar month M**; monthly
headline features are strictly lagged to ≤ M−1. No feature uses post-origin info.

## Findings (backtest 2022-09 .. 2026-04, n≈44, expanding-window RidgeCV)

| Predictor | MAE | RMSE | %≤12.5k | dir% |
|---|---:|---:|---:|---:|
| ridge: momentum | **70.6k** | 94.8k | 6.8 | 84.1 |
| baseline: last value (RW) | 76.9k | 105.3k | 11.4 | 81.8 |
| ridge: mom+claims | 84.2k | 109.6k | 9.1 | 77.3 |
| baseline: trailing-3 mean | 104.1k | 134.8k | 4.5 | 79.5 |
| baseline: long-run mean | 111.2k | 132.3k | 4.5 | 79.5 |
| ridge: claims (monthly) | 169.1k | 191.0k | 2.3 | 79.5 |
| ridge: trends (monthly) | 252.2k | 276.5k | 0.0 | 78.0 |

**Honest read on the ±12,500 goal:** not reachable with monthly-resolution
features. The headline's own volatility is ~125k std / 121k mean-abs over the era;
the best monthly model floors at ~70k MAE and essentially nothing lands inside
12.5k. Monthly-aggregated claims and Trends *hurt* — at this resolution they add
noise rather than signal. Direction (gain vs loss) is ~80%+ simply because the
headline is usually positive.

**The one real lever — the weekly NER Pulse.** It is built from the *same payroll
panel* as the headline. On the only 4 months that overlap observed headlines so
far it tracks far tighter than any monthly model:

| month | headline | pulse-implied (naive ×weeks) |
|---|---:|---:|
| 2026-01 | +11k | +19k |
| 2026-02 | +66k | +51k |
| 2026-03 | +61k | +92k |
| 2026-04 | +109k | +143k |

Pulse-implied MAE ≈ **21.7k** on the overlap with a stable headline/implied ratio
≈ **0.83** — i.e. a fitted-scale Pulse bridge plausibly reaches the 20–30k range,
the best path we have toward the goal. It is data-starved today (Pulse history
starts 2026-01) but strengthens with every weekly collection.

## Recommended direction

1. **Center the methodology on a calibrated Pulse → headline bridge** (fitted
   scale + small AR/claims correction), not a monthly ML model.
2. **Let PIT history accrue.** Score first-prints as they land; revisit once the
   Pulse bridge has ~12+ months and we have real first-print targets.
3. **Productionize** (next phase) mirroring the claims pattern: a BigQuery
   PIT actuals view + a thin forecast pipeline writing to a `forecast_*` table.

## Files

- `data.py` — read-only BigQuery pulls (latest vintage per period).
- `panel.py` — monthly panel construction + PIT-correct feature groups.
- `harness.py` — walk-forward backtest, baselines, Pulse-bridge report, live forecast.
