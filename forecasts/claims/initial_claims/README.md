# Initial claims forecast (level + direction)

**Target:** SA initial jobless claims, weekly (released Thursdays 08:30 ET).
Outputs land in `claims.forecast_sa_initial_claims` (partitioned by
`data_through`); the `_current` view exposes the newest generation.

1. **Level forecast (h=1..13)** — BigQuery ML champion ensemble, produced by
   the stored procedure `claims.fct_forecast_sa_initial_claims()`:
   `0.6 * ARIMA_PLUS + 0.4 * seasonal-naive` ("ens_w60"), training floor
   2023-01-01. Also emits a parallel pure TimesFM 2.5 column (`timesfm_sa`).
   Driven by the **`claims_ens_arima_forecast` BigQuery Scheduled Query**
   (`every thu 17:00` UTC), which is **not** under Terraform — it was created
   by hand in the console on 2026-05-20.
2. **Direction (h=1)** — `direction_lgbm/` (this package). **SUSPENDED
   2026-10-26**, see below.

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

## Level forecast — live accuracy as of 2026-10-26

Scored against first-print SA (`fct_actuals_as_reported`), deduped to the
latest generation per origin, origins 2026-04-18 → 2026-10-10. MAE in claims:

| | h=1 (n=24) | h=2–4 (n=66) | h=5–13 (n=144) |
|---|---|---|---|
| **`sa_forecast` = ens_w60 (primary)** | **15,691** | **17,849** | **23,578** |
| `timesfm_sa` (parallel, unused) | **5,337** | **8,900** | 15,022 |
| Random walk (carry origin) | 6,417 | 9,909 | **12,667** |
| `arima_sa` leg alone | 9,745 | 13,124 | 20,991 |
| `snaive_sa` leg alone | 25,708 | 25,015 | 27,458 |

**The production primary has degraded, and it is entirely bias.** At h=1 the
ens_w60 bias is **+15,691 against an MAE of 15,691** — every error is
positive; it has over-forecast every week for six months. Claims trended down
through 2026, so the 40%-weighted seasonal-naive leg (last year's value) runs
+25.7k high by construction and ARIMA adds +9.0k of drift on top. The
documented backtest was ~8.3–8.9k, i.e. live is ~1.8×.

**TimesFM 2.5, running in parallel and unused, is the best thing here at
h=1–4** — 5,337 MAE, better than the 6,800 its own backtest predicted, and 66%
better than the primary. It also beats the ~6,400 h=1 ceiling the LGBM
research concluded was achievable. At h≥5 nothing beats a plain random walk.
Promoting TimesFM at h=1–4 (and RW or a TimesFM/RW blend at h≥5) is the open
recommendation; the "only validated at h=1" caveat is now stale, since this is
24 weeks of live evidence rather than a backtest.

## Tried and rejected / revisit

- **Isotonic calibration** — needs ~1000+ points; on 26 weeks it memorised
  extremes. Replaced by Platt in 2026-06.
- **COVID masking** (iter-7) — explicit masks hurt the post-2022 model;
  a hard 2022 training floor works better.
- DOLETA future-dated seasonal factors are the quiet dependency: the SA input
  for the target week comes from published factors, not a model. They
  currently run through 2026-12-26.
