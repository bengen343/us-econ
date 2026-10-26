# Claims bucket probabilities (TimesFM 3.0)

**Target:** the *advance* (first-print) SA initial-claims headline — the figure
Kalshi's `KXJOBLESSCLAIMS` contracts settle on, published Thursdays 08:30 ET at
[dol.gov/ui/data.pdf](https://www.dol.gov/ui/data.pdf).

**Output:** `claims.forecast_claims_buckets` (+ `_current` view), one row per
(horizon, 5k strike), append-only, partitioned by `data_through`.

| column | meaning |
|---|---|
| `p_at_least` | P(headline ≥ `bucket_low`) — **one Kalshi contract**, join on `bucket_low = floor_strike` |
| `p_bucket` | P(`bucket_low` ≤ headline < `bucket_high`) — the differenced exceedance curve |

Job `claims-bucket-probs` runs Thursdays 17:15 UTC, right after the collector
lands that morning's release. Its h=1 row targets the *following* Thursday,
whose market closes 12:25 UTC that day — about 6.5 days of lead, and no new
claims data arrives in between, so the Thursday-afternoon run is the final word.

## Why this shape

Kalshi lists ~11 **"At least N"** binaries per weekly event, 5,000 apart on
round multiples of 5,000, re-centred each week (e.g. 2026-10-29: 175k–225k).
They are an exceedance ladder, not a partition — a "lands in 195–200k" view is
the difference of two adjacent strikes. We emit 13 strikes (±30k around the
point forecast) so the listed ladder is always covered.

## Method

1. `AI.FORECAST` with `model => 'TimesFM 3.0'` over `claims.fct_sa_input`,
   2023-01-01 floor, `confidence_level => 0.95`. Zero-shot — no training step.
2. Quantile-match a **two-piece normal** to (lower, point, upper): the point
   forecast is the median, and each bound is its stated quantile, giving
   `sigma_lo = (point − lower)/1.96` and `sigma_hi = (upper − point)/1.96`.
3. Integrate over the strike grid, correcting for the fact that DOL rounds the
   headline to the nearest 1,000: `P(published ≥ 195,000)` is
   `P(underlying ≥ 194,500)`.

### The two choices that matter

**Median, not mode.** TimesFM's interval is right-skewed (mean `sigma_hi`
~10.2k vs `sigma_lo` ~7.9k). Treating the point forecast as the *mode* of a
proper split-normal density — the other natural reading — puts only ~46% of the
mass below it and tilts every probability upward. Over 119 origins that fails a
PIT uniformity test (**KS p = 0.004**, mean PIT 0.419); quantile matching passes
(**KS p = 0.215**). This cost a real calibration bug, so don't "fix" it back.

**The rounding correction.** Skipping it biases every strike by half a rounding
unit — 10% of a bucket width.

**No fitted parameters.** Walk-forward scalar bias correction and a sigma-scale
factor were both tested on top of quantile matching and both made calibration
*worse* (KS p 0.215 → 0.015 and 0.171). That matches the project's wider
finding: with ~119 weekly observations against a strong zero-shot baseline,
anything estimated injects more variance than it removes.

### Why 3.0 here when the level forecast runs 2.5

These two jobs deliberately use different TimesFM versions. The level forecast
is scored on MAE, where **2.5 wins at every horizon 1..13**; this job is scored
on calibration, where the two are near-identical except in the one place that
matters for pricing. Same 119 origins, same quantile-matched fit, h=1:

| | TimesFM 2.5 | TimesFM 3.0 |
|---|---|---|
| PIT KS p | 0.211 | 0.215 |
| Brier (exceedance) | **0.0746** | 0.0764 |
| Log loss | **0.2583** | 0.2625 |
| Modal-bucket hit | 26.1% | 26.1% |
| MAE | **6,633** | 6,802 |
| 95% interval coverage | 91.6% | **95.0%** |
| PIT > 0.975 (expect ~3) | **7** | 4 |

2.5 is marginally better on every aggregate score and **wrong in the upper
tail**: 7 of 119 actuals landed above its 97.5th percentile against ~3
expected, because its `sigma_hi` is 9,439 against 3.0's 10,246. That tail is
not incidental here — every one of this series' worst weeks (hurricanes, July
auto shutdowns, holiday-shifted weeks) is a *positive* spike, and the contracts
most exposed to it are the far "At least N" strikes. An underpriced upper tail
is the one calibration error that loses money systematically, so 3.0 stays.

It is a close call: on Brier and log loss, 2.5 is the better model. If you
revisit it, revisit it on tail coverage, not on the headline scores. Note also
that the two jobs will publish slightly different point forecasts for the same
week (e.g. 2026-10-24: 197,389 from 2.5 vs 196,969 from 3.0) — that is expected,
not a bug.

## Validation (119 walk-forward origins, 2024-07-06 → 2026-10-10)

Scored against first-print SA, 1,547 exceedance contracts:

| | |
|---|---|
| PIT uniformity | KS 0.095, **p = 0.215** (calibrated) |
| Brier (exceedance) | **0.0764** vs 0.2486 climatology — **+69.3% skill** |
| Log loss | 0.2625 |
| Modal-bucket probability | 23.1% stated vs **26.1%** realised (mildly conservative) |
| Mean P assigned to the bucket that occurred | 16.3% |

Reliability tracks the diagonal at the tails and is mildly over-predicting in
the thin 0.25–0.55 mid-bands (50–75 observations each), which is where to look
first if calibration drifts.

**Expect ~22–26% on the modal bucket. That is near the ceiling, not a weak
model.** With a residual sigma of ~9.1k against 5k buckets, the best attainable
hit rate for *any* unbiased point forecast is ~21–23%; TimesFM 3.0 realises
25.2%. The value here is the calibrated distribution, not the point call.

## Scope notes

- Runs its own `AI.FORECAST` rather than reading `forecast_sa_initial_claims`.
  The suspended direction job failed in exactly that shape — it depended on a
  separate Scheduled Query having already written the row it wanted. One fewer
  ordering dependency.
- Hard-fails if `fct_sa_input` is more than 10 days stale rather than pricing a
  live market off a dead collector.
- Does **not** read Kalshi. `bucket_low` is deliberately the same integer as
  Kalshi's `floor_strike`, so comparing model probability to market price is a
  join away, but pulling prices is a separate piece of work.
- `fct_forecast_sa_initial_claims` promoted TimesFM 2.5 to `sa_forecast` on
  2026-10-26, replacing ens_w60 (which scored 0-for-24 on this bucket metric).
  The two jobs stay independent on purpose, so this one is unaffected by a
  champion change there.
