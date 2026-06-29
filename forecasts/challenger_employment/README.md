# Challenger job-cuts headline forecast

**Target:** the headline Challenger, Gray & Christmas announced job cuts (layoffs,
total persons, NSA) for month M, released ~the first Thursday of M+1 (07:30 ET).
Because the month is already over by release day, this is a **nowcast of a
completed month** — all of month M's jobless claims, ISM employment, and
consumer-sentiment readings are published before Challenger reports. Job
`challenger-job-cuts-forecast` runs daily in the first week at 05:00 MT, writing
the level + m/m change to `challenger_employment.forecast_job_cuts` (+`_current`
view) and re-nowcasting each day as the month's indicators land.

## Winning model (`seas_ensemble_v1`)

The target is modelled in **logs** (the level is right-skewed, ~15k–670k). The
forecast is a log-space **ensemble** of two standardized-ridge regressions on
11 month dummies + the prior-month log headline (`log y_{M-1}`):

- **`ism`** — + ISM-manufacturing-employment deviation (`50 − index`)   [λ=5]
- **`allind`** — `ism` + initial-claims YoY + Conference-Board labor differential
  + Michigan sentiment   [λ=15]

`forecast_next` averages whichever members are computable. ISM employment is the
tightest-timed input (it lands on the 1st business day, ~1 day before the
release), so until the month's surveys are in the model **falls back** to the
seasonal-dummy + AR(1) base (`seas_dummies_ar1`), which still beats a random walk
by ~11%. The path taken is logged as `method` ∈ {ensemble, ism_only,
allind_only, fallback}.

### Backtest

Walk-forward expanding-window, COVID (2020-03..2021-06) masked, scored on the
**common 77-month test set (2016-01..2026-05)** so every model is judged on the
same months. Errors in persons (announced cuts):

| model | MAE | RMSE | MdAE | MAPE | dir | vs RW | vs SNaive |
|---|---|---|---|---|---|---|---|
| **ENSEMBLE** (production) | 16,387 | **31,813** | 9,164 | 30.1% | 68% | **−11.0%** | **−41.7%** |
| seasdum_ar1_ism (λ5) | 17,448 | 31,405 | 11,430 | 34.0% | 64% | −12.1% | −42.5% |
| seas_dummies_ar1 (fallback) | 18,010 | 31,848 | 11,597 | 34.3% | 61% | −10.9% | −41.7% |
| seasdum_ar1_allind (λ15) | 16,279 | 32,809 | 6,582 | 29.1% | 70% | −8.2% | −39.9% |
| random walk (y_{M-1}) | 20,473 | 35,731 | 11,350 | 34.5% | — | 0% | −34.6% |
| 3-month moving average | 17,728 | 35,523 | 6,525 | 28.3% | 71% | −0.6% | −34.9% |
| **seasonal-naive (y_{M-12})** | 29,839 | 54,603 | 13,594 | 83.9% | 65% | +52.8% | 0% |

The ensemble is chosen because it **dominates the tradeoff**: it keeps the best
RMSE (within 1% of `seasdum_ar1_ism`) while cutting typical-month error sharply
(MdAE 9.2k vs 11.4k, MAPE 30% vs 34%) and lifting direction to 68%. The two
members are complementary — `ism` is more robust on the lumpy spike months
(better RMSE), `allind` is sharper on the typical month (better MdAE/MAPE/dir).

**Key results:**
- **Seasonal-naive is the *worst* model** — the series swings too much
  year-over-year for "same month last year" to work. Seasonality must be paired
  with recent level (AR) + indicators.
- **ISM manufacturing employment is the single most useful indicator**
  (contemporaneous corr of log-headline ≈ −0.38, far ahead of claims/sentiment).
- **RMSE is bounded by irreducible "lumpy" spikes.** The worst misses are all
  single-event surges no macro indicator can foresee — e.g. Mar-2025 (275k
  actual, ~85k predicted; the federal/DOGE layoff wave), Feb-2025, Oct-2025,
  Jan-2026. On typical months the error is ~9k (MdAE) and direction is strong.

Sub-period stability (ensemble): calm **2016–2019** RMSE 13.7k / MAPE 23.7% /
dir 65%; turbulent **2021H2–2026** RMSE 42k / MAPE 37.3% / dir 70% — the higher
errors are entirely the 2023–2025 mega-layoff era, where direction still holds.

Live check: with May-2026 (97,006) as the last actual, the model nowcasts
June-2026 at **~69,000** (fallback, as June ISM employment is not yet out); the
full ensemble lands ~July 1 when ISM releases, one day before the Challenger
report.

## Data flow

Production reads BigQuery only, all features aligned to month M and observable
before the M+1 release:

- **Target** — `challenger_employment.monthly` (`series='layoffs',
  breakdown='total'`). The live collector history was extended with a one-off
  **Wayback backfill** of archived report PDFs (parsed with the production
  parser's "MONTH BY MONTH TOTALS" extractor) to give **153 monthly points,
  2012–2026**. Validated to the dollar against the live collector (15/15) and the
  quarterly table (9/9), which also confirmed **the headline does not revise**.
  Gap: all of 2023 + a few scattered months (the 2023 report PDFs aren't in the
  archive) — 20 months absent, harmless to the backtest.
- **`claims.weekly_claims`** — national NSA initial claims (2006+), monthly mean.
- **`ism.report_on_business`** — ISM manufacturing employment index (1948+).
- **`conference_board.consumer_confidence`** — labor differential (1967+).
- **`michigan_sentiment.surveys_of_consumers`** — sentiment, final (1952+).

No new collectors were needed; every input was already collected.

## What lost / was excluded

- **WARN advance-layoff notices** — the research's best-validated lead (Cleveland
  Fed: leads claims/UR/employment), but national aggregation across 50 state
  feeds is high-effort; skipped by decision. The strongest candidate if a future
  v2 wants to push past the current accuracy.
- **News/SEC "big-announcement" overlay** — the lumpy spikes that cap RMSE are
  often pre-known from press/SEC filings (e.g. Jan-2026 UPS + Amazon = 42% of the
  month). Netting these out is the other obvious v2 lever; out of scope for v1.
- **Google Trends "layoffs"** — promising news proxy but the series only starts
  2021, too short for the 2012–2026 backtest.
- **S&P 500 monthly return** — no edge in the bake-off (`seasAR_sp` ≈ the AR
  baseline); dropped so the job has no `market_indexes` dependency.
- **Seasonal ARIMA / richer AR** — the seasonal-dummy + AR(1) core already
  captures the usable own-history signal; extra lags (`ar2_seas`) did not help.

## Research background

No prior published model targets the Challenger headline directly. The series is
monthly, **not seasonally adjusted**, an aggregation of individually announced
corporate cuts (press releases, news, SEC filings) — so it is highly volatile,
strongly seasonal (Q1 elevated as Q4 planning surfaces; December trough), atop a
declining secular baseline, and **"lumpy"** (a few large single-employer
announcements drive 40%+ of a month). Announced cuts Granger-cause JOLTS layoffs
(quarterly) and the 3-month MA correlates ~67% with the 3-month payroll change.
These properties motivated the design: explicit seasonality + recent level, log
scale, and same-month leading indicators, with the understanding that
single-event spikes are largely irreducible. (See memory:
`project_challenger_forecast`.)

## Files

- `model.py` — `build_features` (canonical feature set) + `forecast_next`
  (ensemble with fallback). Shared by research and production.
- `data.py` — BigQuery panel: target + the four model indicators.
- `harness.py` — the walk-forward bake-off (delegates to `model.build_features`,
  so research and production features cannot drift). Run:
  `.venv\Scripts\python.exe -m forecasts.challenger_employment.harness`.
- `production/` — the Cloud Run Job (`config`, `models`, `main`).

## Caveats / revisit

- **2023 is missing from history** (archive gap); the live collector fills 2025
  onward, so the hole only shrinks the backtest sample — it does not affect
  production, which needs just the trailing months.
- **RMSE is dominated by a handful of policy/AI-driven spikes** (2025 federal
  layoffs). Direction and typical-month error are the honest scorecard; the point
  forecast will systematically under-call a genuine mega-announcement month.
- **Tight release timing**: ISM employment lands ~1 day before Challenger, so the
  high-quality ensemble window is narrow; the fallback (still −11% vs RW) covers
  the rest. If ISM is ever delayed past the release, the fallback carries.
- A future **v2** would add WARN and/or a news/SEC big-announcement overlay — the
  two levers most likely to attack the irreducible-spike ceiling.
