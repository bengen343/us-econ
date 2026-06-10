# ADP national monthly headline forecast

**Target:** MoM change in the ADP NER SA private-employment level
(`adp_employment.ner_history`), released the first Wednesday of M+1. Horizon
h=1. **Goal:** ±12.5k (stretch).

Two sub-packages, each with its own detailed README:

- **`research/`** — the monthly-feature harness that established the ceiling:
  walk-forward RidgeCV over momentum / claims / Google Trends specs,
  2022-09..2026-04 (~44 origins). Best monthly spec (`momentum`) lands at
  **MAE 70.6k vs RW 76.9k** — nowhere near ±12.5k. Claims (169k) and Trends
  (252k) add noise at monthly resolution. Verdict: monthly-aggregated features
  floor at ~70k; the only real lever is sub-monthly information from the
  weekly NER Pulse, which is built from the same payroll panel as the
  headline.
- **`pulse_bridge/`** — the production model that uses that lever
  (`pulse_bridge_v2`, job `forecast-adp-headline-pulse`, Tuesdays 07:30 MT
  after the weekly Pulse collection, writes
  `adp_employment.forecast_national_monthly` + `_current`).

## How the bridge works (v2)

`forecast(M) = w · b · implied(M) + (1−w) · prior(M)` where `implied(M)` is
the Pulse 4-week-MA run-rate scaled to the month's expected weeks, `prior(M)`
is a random walk on the last headline, and `w = Pulse completeness` (observed
weeks / expected, linear). The scale `b` is calibrated on post-benchmark-break
months (floor 2026-01; ADP's Jan-2026 restatement invalidates earlier ratios)
with **maturity-decayed shrinkage** toward the B0=1.0 prior: pseudo-count
`k = 3.0 · (1 − completeness)`, so early-month forecasts stay anchored while
late-month forecasts trust the observed pooled ratio (~0.73-0.83).

v2 exists because v1's fixed shrinkage dragged the scale to ~0.9 while the
live ratio sat ~0.72-0.75 → systematic late-month overshoot (Apr/May 2026).
Key data fact: **Pulse vintages never revise** — there is no revision curve to
learn, only completeness to weight by.

## Performance

- Leave-one-out, post-break complete-Pulse months: **MAE 16.5k (v2)** vs 18.1k
  (fixed shrinkage) vs 16.9k (no prior).
- Pulse-implied alone on the 4-month overlap (Jan-Apr 2026): MAE ~21.7k with a
  stable headline/implied ratio ~0.83.
- For context, the best monthly-feature model: 70.6k. The bridge is the
  difference between "nowhere close" and "within sight" of the ±12.5k goal.

## Caveats / revisit

- **Data-starved by construction:** Pulse history starts 2026-01, one monthly
  vintage existed at build time, the calibration window is a handful of
  months. Every weekly collection strengthens it; re-examine the calibration
  (and BLEND_GAMMA=1.0) once ~12 months of post-break Pulse months exist.
- Backtests score against the revised SA level as a proxy for first prints;
  true first-print history accrues one release at a time.
- A small AR/claims correction on the bridge residual is the next idea in the
  research README — untested, waiting on sample size.
