# Initial claims forecast (level + direction)

**Target:** SA initial jobless claims, weekly (released Thursdays 08:30 ET).
Two complementary outputs in `claims.forecast_sa_initial_claims` (partitioned
by `data_through`):

1. **Level forecast (h=1..13)** — BigQuery ML champion ensemble, produced by
   the upstream stored procedure: `0.6 * ARIMA_PLUS + 0.4 * seasonal-naive`
   ("ens_w60"), training floor 2023-01-01.
2. **Direction (h=1)** — `direction_lgbm/` (this package): probability the
   next SA print exceeds the origin-week SA input. Updates the direction
   columns (`pred_dir_up`, `p_up_raw`, `p_up_calibrated`) on the latest level
   row; fails loudly if the level row doesn't exist yet.

Job `forecast-direction-initial-claims` runs Thursdays 17:30 UTC, after the
release and the level procedure.

## Direction model (`adp+tr4`, iter-13)

LightGBM binary classifier, walk-forward, trained post-2022 only (pre-2022
data hurt even with COVID masking). Features: SA lags 1-8, w/w diffs (1/2/4),
rolling means (4/8), 52-week seasonals, calendar, 11 Google Trends signals at
lag 4, and the ADP weekly NER w/w diff at lag 8 (fallback 9-12 — ADP publishes
with ~53-day lag).

**Calibration:** walk-forward **Platt scaling** over all prior held-out
predictions, clipped to (0.05, 0.95). Replaced the original 26-week rolling
isotonic in 2026-06 after it produced extreme-and-wrong calls (p_up=0.001 on a
+13k week): out-of-sample over ~150 origins, Platt Brier 0.249 / logloss 0.69 /
zero extreme-and-wrong vs isotonic 0.289 / 1.76 / 8 and raw 0.358 / 2.16 / 36.

## Performance

- **Direction: 69.1% h=1 hit rate** over 97 walk-forward origins
  (2024-07..2026-05).
- **Level (champion ens_w60): MAE ~8.9k / MASE ~0.77** post-COVID across
  h=1..13.
- Directional-forecast levers verified in the 2026-06 review: the Platt
  recalibration above, plus PIT-clean feature timing.

## Tried and rejected / revisit

- **Isotonic calibration** — needs ~1000+ points; on 26 weeks it memorised
  extremes. Replaced (see above).
- **COVID masking** (iter-7) — explicit masks hurt the post-2022 model;
  a hard 2022 training floor works better.
- **TimesFM 2.5 at h=1: MAE ~6.8k vs ens_w60 ~8.3k (−18%)** — the strongest
  pending upgrade for the *level* forecast, but only validated at h=1, and the
  series is famous enough to be in TimesFM's pretraining corpus (zero-shot
  number may be optimistic). Revisit: validate h=2..13 live before promoting;
  blends (30/70, 50/50, 70/30) tested in phase 2 didn't justify a switch yet.
- Calibration refits grow by one per week (expanding window, ~0.3s each) —
  fine for years at the 600s job budget.
- DOLETA future-dated seasonal factors are the quiet dependency: the SA input
  for the target week comes from published factors, not a model.
