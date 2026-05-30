# NFP headline nowcast (research)

**Target:** MoM change in `CES0000000001` (SA total nonfarm payrolls, thousands).
**Origin:** first Friday of M+1 (release eve). **Goal:** MoM within **10k** (stretch).

## PIT feature groups (no leak)
- `momentum` — NFP changes ≤ M-1 (lags, rolling means).
- `claims` — initial/continued SA claims averaged over weeks ending in M.
- `adp` — ADP monthly headline for M (released ~2 days before NFP; 2010+).
- `temphelp` — temp-help employment MoM, lagged ≤ M-1 (ships with the release).
- `trends` — Google Trends labor-stress/hiring composites (2021+).
- `challenger` — announced job cuts / hiring (layoffs ~15 mo only → recent-only).

## Backtest verdict (2026-05-29, 168 origins 2011-01..2026-04, COVID-masked)

Target in the test window: mean +205k, std 153k, mean|·| **210k**.

Leaderboard by MAE (thousands):

| method | MAE | RMSE | %<10k | n |
|---|--:|--:|--:|--:|
| **model: mom+claims** | **82.0** | 106.4 | 8 | 168 |
| model: mom+claims+adp | 83.4 | 117.3 | 8 | 142 |
| model: mom+claims+temphelp | 83.8 | 108.6 | 7 | 168 |
| baseline: mean3 | 87.9 | 125.0 | 10 | 168 |
| model: mom+adp | 88.4 | 126.7 | 6 | 142 |
| model: adp | 92.0 | 129.9 | 4 | 142 |
| model: momentum | 92.4 | 122.1 | 7 | 168 |
| baseline: adp_direct | 95.6 | 127.2 | 7 | 168 |
| model: claims | 115.0 | 149.5 | 7 | 168 |
| baseline: rw | 115.6 | 155.8 | 7 | 168 |
| baseline: snaive | 145.1 | 206.6 | 8 | 168 |
| baseline: zero | 210.0 | 255.0 | 2 | 168 |

**The 10k goal is not reachable at monthly resolution** — best %<10k is ~8-10%,
indistinguishable from the trailing-mean baseline's lucky hits. This mirrors the
ADP headline finding (±12.5k unreachable monthly); NFP is the harder target
(higher variance) so the realistic floor is worse.

Findings:
- **Best deep-history model: `mom+claims` ≈ 82k MAE**, beating RW (116k) and the
  trailing-3-month mean (88k) by a modest but real margin. Momentum + weekly
  claims carry essentially all the monthly-resolution signal.
- **ADP adds ~nothing** beyond momentum+claims (83.4 vs 82.0 on its 142-origin
  subset). ADP is itself a monthly nowcast of the same thing — redundant with
  claims-based signal rather than additive. `adp_direct` (use ADP's print as the
  NFP forecast) is a respectable 95.6k but worse than the fitted models.
- **Trends and Challenger don't help** (small-n, noisy — Trends-only is 227k).
- Direction (gain/loss) is ~96%, but trivial — NFP is almost always positive.

### BigQuery-native ML survey (`timesfm_bench.py`, `bqml_bench.py`)

Three BQ-native approaches were benchmarked against the linear harness. **None
beat RidgeCV.**

| method | MAE | notes |
|---|--:|---|
| **Ridge `mom+claims` (winner)** | **82.0** | linear, engineered features, 168 origins |
| TimesFM 2.5, change-direct (`AI.FORECAST`) | 86.3 | univariate FM; ties trailing-mean |
| TimesFM 2.5, level-then-diff | 161.1 | never difference a level forecast |
| BOOSTED_TREE_REGRESSOR | 104.8 | overfits at n≈150 monthly rows |
| ARIMA_PLUS_XREG (matched 2016+) | 127.0 | ≈ RW (132.4); Ridge on same origins 90.1 |

- **TimesFM**: forecast the *change* directly (86k) — level-then-diff (161k)
  amplifies error. Still can't see the weekly-claims signal, so it trails the
  feature model. (Contrast weekly initial claims, where TimesFM excelled: that
  target is a high-autocorrelation *level*, its sweet spot; NFP MoM is a noisy
  difference.)
- **BOOSTED_TREE**: nonlinearity doesn't pay at this sample size — it overfits
  where regularised linear Ridge holds.
- **ARIMA_PLUS_XREG**: classical dynamics + the same regressors land at ≈ random
  walk; the auto-ARIMA terms add noise rather than signal.

**The lever (same as ADP):** sub-monthly information. A within-month run-rate
from a weekly payroll signal (ADP Pulse) is the only thing that could plausibly
tighten this, and it is data-starved (~16 weeks). It grows each weekly release.

**Live forecast (May-2026, released ~first Friday Jun-2026):** `mom+claims`
≈ **+72k**. (ADP for May not yet published at this origin.)
