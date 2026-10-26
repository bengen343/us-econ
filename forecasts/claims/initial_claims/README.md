# Initial claims forecast (level + buckets + direction)

**Target:** SA initial jobless claims, weekly (released Thursdays 08:30 ET).
Outputs land in `claims.forecast_sa_initial_claims` (partitioned by
`data_through`); the `_current` view exposes the newest generation.

1. **Level forecast (h=1..13)** — produced by the stored procedure
   `claims.fct_forecast_sa_initial_claims()` (`10_production_forecast.sql`).
   Champion is **pure TimesFM 2.5** (`AI.FORECAST`, zero-shot, 2023-01-01
   context floor) as of **2026-10-26**; `0.6 * ARIMA_PLUS + 0.4 *
   seasonal-naive` ("ens_w60") was demoted that day and survives only as its
   component columns. Driven by the **`claims_ens_arima_forecast` BigQuery
   Scheduled Query** (`every thu 17:00` UTC), which is **not** under
   Terraform — it was created by hand in the console on 2026-05-20, and its
   name is now a misnomer.
2. **Bucket probabilities (h=1..4)** — `bucket_probs/` (Cloud Run Job,
   Thursdays 17:15 UTC). A calibrated distribution over Kalshi's 5k strike
   ladder, from **TimesFM 3.0**. See that package's README, including why it
   uses a different TimesFM version from the level forecast.
3. **Direction (h=1)** — `direction_lgbm/` (this package). **SUSPENDED
   2026-10-26**, see below.

### Reading `sa_forecast` across the champion change

`sa_forecast` means ens_w60 before 2026-10-26 and TimesFM 2.5 after, so every
row now carries a **`model_version`** column (`'ens_w60'` backfilled on
history, `'timesfm25_v1'` going forward). Group by it before computing any
accuracy figure over the table's full history, or you are averaging two
different models. `timesfm_sa` keeps its own continuous history and from
2026-10-26 is identical to `sa_forecast` by construction.

## Direction model — SUSPENDED 2026-10-26

The Cloud Run Job `forecast-direction-initial-claims` and its scheduler were
removed from `terraform/forecasts.tf`. The package is retained; restoring the
module block and re-applying is the whole revival path.

**Why it was suspended — two independent reasons:**

1. **Its inputs died.** 11 of the model's 29 features are Google Trends
   signals at lag 4. `google_trends.weekly` stopped at week ending
   **2026-07-18** when the off-platform host that fed it stopped, and it is
   not being restored (see the root README). From mid-August every inference
   row carried **11/29 features as `NaN`** while the model continued training
   on rows that were 97.6% populated — a straight train/serve skew across 38%
   of the feature vector. It never failed; it just stopped meaning anything.
   The visible symptom was saturated output: `p_up_raw` was 0.000 for five
   consecutive weeks in Sept/Oct, then 0.998.
2. **It did not work live even before that.** Over 22 scored weeks
   (2026-05-16 .. 2026-10-17):

   | Metric | Live | Backtest claim |
   |---|---|---|
   | h=1 hit rate | **45.5%** (10/22) | 69.1% (n=97) |
   | "always predict down" | 63.6% (14/22) | — |
   | Brier, `p_up_raw` | 0.451 | — |
   | Brier, `p_up_calibrated` | 0.268 | 0.249 |
   | Saturated calls (p<0.01 or >0.99) | 12/22 | "zero extreme-and-wrong" |

   45.5% held **both** before and after the Trends outage (5/11 in each half),
   so the outage is not the whole story — it only made the probabilities
   wilder (4 → 8 saturated calls). Scoring against the origin's first print
   instead of its revised value gives 9/22, so this is not a revision
   artifact. `pred_dir_up` thresholds on the **raw** probability, which is the
   broken channel; the Platt-calibrated channel was behaving (Brier 0.268).

**Data contract while suspended:** `pred_dir_up`, `p_up_raw`,
`p_up_calibrated`, `dir_feature_set` and `dir_n_train_origins` remain as
columns and keep their history, but are **NULL on every generation from
2026-10-29 onward**. `forecast_sa_initial_claims_current` is `SELECT *`, so
consumers must treat NULL as "not produced", not as "no signal".

**Before reviving:** the ADP dependency had already degraded from the modelled
lag-8 to lag-9 by 2026-10-17, and `resolve_adp_diff_lag` hard-fails once the
freshest ADP week falls outside lag 8..12 — roughly one week of slack past a
missed ADP monthly release. Also note `features.trim_features()` is dead code
(never called by `main.py`), which is *why* NaN-feature rows survive into
training.

## Level forecast — why ens_w60 was demoted (2026-10-26)

**Live**, scored against first-print SA (`fct_actuals_as_reported`), deduped to
the latest generation per origin, origins 2026-04-18 → 2026-10-10:

| | h=1 (n=24) | h=2–4 (n=66) | h=5–13 (n=144) |
|---|---|---|---|
| `sa_forecast` = ens_w60 (old primary) | **15,691** | **17,849** | **23,578** |
| `timesfm_sa` (TimesFM 2.5) | **5,337** | **8,900** | 15,022 |
| Random walk (carry origin) | 6,417 | 9,909 | **12,667** |
| `arima_sa` leg alone | 9,745 | 13,124 | 20,991 |
| `snaive_sa` leg alone | 25,708 | 25,015 | 27,458 |

**The old primary had degraded, and it was entirely bias.** At h=1 the ens_w60
bias was **+15,691 against an MAE of 15,691** — every error positive; it
over-forecast every week for six months. Claims trended down through 2026, so
the 40%-weighted seasonal-naive leg (last year's value) runs +25.7k high by
construction and ARIMA added +9.0k of drift on top. The documented backtest was
~8.3–8.9k, i.e. live was ~1.8×.

### Choosing the replacement

The live table above is only 24 origins, so the promotion was decided on a
**119-origin walk-forward** (2024-07-06 → 2026-10-10, same first-print target),
not on it. MAE by horizon:

| h | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | mean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **TimesFM 2.5** | **6,629** | **7,685** | **8,460** | **8,648** | **8,983** | **9,068** | **9,258** | **9,471** | **9,737** | **9,926** | **10,163** | **10,396** | **10,479** | **9,146** |
| TimesFM 3.0 | 6,802 | 7,886 | 8,675 | 8,855 | 9,366 | 9,590 | 9,971 | 10,217 | 10,534 | 10,840 | 11,178 | 11,612 | 11,785 | 9,793 |
| Random walk | 7,529 | 9,347 | 10,752 | 10,750 | 11,139 | 11,763 | 11,292 | 11,080 | 10,865 | 11,600 | 11,202 | 11,593 | 11,393 | 10,793 |
| Seasonal naive | 14,252 | 14,271 | 14,342 | 14,379 | 14,296 | 14,246 | 14,301 | 14,411 | 14,523 | 14,636 | 14,661 | 14,750 | 14,785 | 14,450 |

**TimesFM 2.5 wins at every horizon**, including over TimesFM 3.0 — so the
level forecast and the bucket job deliberately run different TimesFM versions
(see `bucket_probs/README.md`). The "only validated at h=1" caveat that kept
ens_w60 in place is retired.

**No RW blend at long horizons, despite the live table.** RW's h≥5 advantage is
one regime, not a result: on origins from 2026-04-18 it holds (RW 13,364 vs
TimesFM 14,714, n=162) and on the earlier 837 observations it reverses hard
(10,930 vs 8,753). A horizon-switching rule fitted to ~160 observations of a
single down-trending stretch is exactly the shape of the nine information-
addition levers this project has already killed.

ARIMA_PLUS is still trained each run and `arima_sa` / `snaive_sa` still stored,
so `0.6 * arima_sa + 0.4 * snaive_sa` reconstructs the demoted blend exactly
and keeps scoring against the champion.

## Tried and rejected / revisit

- **Isotonic calibration** — needs ~1000+ points; on 26 weeks it memorised
  extremes. Replaced by Platt in 2026-06.
- **COVID masking** (iter-7) — explicit masks hurt the post-2022 model;
  a hard 2022 training floor works better.
- DOLETA future-dated seasonal factors are the quiet dependency: the SA input
  for the target week comes from published factors, not a model. They
  currently run through 2026-12-26.
