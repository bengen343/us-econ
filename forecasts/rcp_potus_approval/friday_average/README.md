# RCP Friday approval-average forecast

Forecasts the **RealClearPolitics presidential-approval average** (the `approve`
figure) for the **upcoming Friday**. The forecast is made daily Sat → Thu and
refined each day as the week's polls land, so a given Friday is forecast six
times at horizons **h = 6 (prior Sat) … h = 1 (Thu)**.

## What we're forecasting, and why it's tractable

RCP publishes no methodology, but we reverse-engineered it from 17 months of
archived poll tables (see the project memory / scratch analysis):

- The published average is the **simple unweighted mean of the polls currently
  listed on the page** — verified to 0.1 on every overlapping day.
- The poll set changes only by **entries** and **exits**. A pollster's new poll
  **replaces** its prior poll (deterministic); old polls are **pruned** with a
  hazard that rises with poll age (no fixed time cutoff). Window size is ~stable.
- Poll values are dominated by pollster **house effects**; within-pollster
  poll-to-poll change is small.

So most of Friday's average is already determined days ahead: at h = 1, ~87% of
Friday's window is already on the page; at h = 6, ~44%. The forecast's job is to
project the remaining churn.

## Model (`struct_blend_v1`)

A horizon-weighted blend of two components:

```
forecast = wc·(A_D + ½·drift·h)  +  (1 − wc)·structural
wc = (7 − h) / 6      # 1.0 at h=1  →  0.17 at h=6
```

- **Drift-corrected carry-forward** — the current published average `A_D` plus a
  shrunk local trend (½ × the trailing-14-day slope). Carry-forward is near
  unbeatable at short horizons (the average is close to a random walk day to day);
  the half-drift removes its systematic lag.
- **Structural Monte-Carlo** (`model.simulate`) — marks each future day with
  per-pollster **renewal release hazards** (discrete-time hazard on days since a
  pollster's last release — captures Rasmussen's near-daily tracker, RMG's Friday
  cadence, the weekly Economist/YouGov & Morning Consult, etc.), applies
  **replacement** + the age-based **prune hazard**, and reports the mean of the
  surviving window. Dormant and infrequent pollsters are gated so the simulated
  window turnover matches the observed (~flat) rate. Values use pollster house
  offsets around a locally-drifting latent level.

The blend leans on carry-forward early in the week and on the structural model as
the horizon grows and more of the window turns over. Hyperparameters live in
[`model.py`](model.py); the I/O config in [`production/config.py`](production/config.py).

## Backtest — error by horizon

Walk-forward over the 2nd term (origins = snapshot-capture days; target = the
upcoming Friday's published average), produced by
[`harness.py`](harness.py) on the same BigQuery data the production job reads:

```
.\.venv\Scripts\python.exe -m forecasts.rcp_potus_approval.friday_average.harness
```

| horizon | origin day | n | carry-fwd RMSE | **blend RMSE** | carry MAE | **blend MAE** | carry bias | **blend bias** |
|--------:|:-----------|--:|:--------------:|:--------------:|:---------:|:-------------:|:----------:|:--------------:|
| h = 1 | Thu | 34 | 0.239 | **0.238** | 0.188 | 0.190 | +0.035 | +0.026 |
| h = 2 | Wed | 35 | 0.294 | **0.299** | 0.220 | 0.235 | +0.043 | −0.005 |
| h = 3 | Tue | 32 | 0.457 | **0.478** | 0.359 | 0.392 | +0.097 | +0.042 |
| h = 4 | Mon | 36 | 0.547 | **0.527** | 0.425 | 0.426 | +0.136 | +0.069 |
| h = 5 | Sun | 35 | 0.463 | **0.464** | 0.349 | 0.350 | +0.097 | +0.025 |
| h = 6 | Sat | 30 | 0.634 | **0.592** | 0.490 | 0.469 | +0.250 | +0.134 |
| **horizon-avg** | | | 0.439 | **0.433** | 0.339 | 0.344 | | |

(Units are approval percentage points. RNG-seeded, so reproducible run to run.)

**Reading the table.** Carry-forward is the benchmark, and it is genuinely hard
to beat — Friday-to-Friday the average barely moves (h = 1 MAE ≈ 0.19; ~1-in-5
Fridays it doesn't change at all). The structural blend's value is concentrated
where it should be:

- **Long horizons (h = 4, 6):** the blend wins on RMSE (−4% at h = 4, **−7% at
  h = 6**) — exactly the "start Saturday" end of the cycle where the most of the
  window is still unknown.
- **Calibration at every horizon:** carry-forward systematically runs **high**
  (approval drifted down over this sample; +0.25 bias at h = 6). The blend roughly
  **halves the bias** at every horizon, so the central call is far better centered.
- **Short horizons (h = 1–2):** ~tie. The blend collapses to drift-corrected
  carry-forward there (`wc` ≈ 1), so it never gives up much.

**On the absolute level.** These RMSEs are a touch pessimistic: the historical
backtest targets are Wayback captures taken at *random times of day*, which adds
timing noise to both the origins and the targets. Going forward the collector
captures at a fixed 09:00 MT daily, so live targets are cleaner — and the BQ
`rcp_average` row the collector records each Friday **is** the production target,
so the backtest measures exactly the live objective.

## Production

[`production/main.py`](production/main.py) runs as a daily Cloud Run Job. It is
**BigQuery-only** — it reads the collector's `rcp_potus_approval.polls` snapshot
table and writes the forecast table; it never touches realclearpolling.com, so
(unlike the off-platform collector) it runs on Cloud Run.

Each run:
1. Pulls the snapshot history (read-only) and builds the as-of window, the
   published-average truth series, and per-pollster release history.
2. Computes the blend forecast for the upcoming Friday.
3. Self-bootstraps `rcp_potus_approval.forecast_friday_average` + its
   `_current` view.
4. Upserts one row keyed by `(target_friday, as_of_date, model_version)` —
   idempotent on same-day retry; one new revision row per day as the horizon
   shrinks. The `_current` view surfaces the latest `as_of_date` per
   `target_friday`.

On **Fridays the job idles** (the target realises at that morning's capture; the
next forecast cycle starts Saturday at h = 6). `DRY_RUN=1` computes + logs
without writing (local validation; per repo convention only the deployed Job
writes BigQuery).

The output table also carries the component columns (`carry_forward`,
`drift_corrected_carry`, `structural_mean`, `drift_per_day`) and an 80% band
(`band_lo`/`band_hi`, the 10th/90th percentiles of the structural simulation)
for monitoring and post-hoc scoring.
