# Unemployment-rate nowcast (research)

**Target:** `LNS14000000` (SA civilian unemployment rate, percent), modelled in
**both** framings the user asked for — level and MoM change. **Origin:** first
Friday of M+1. **Goal:** call the published 0.1 **exactly**.

## PIT feature groups (no leak)
- `momentum` — UR level + change lags ≤ M-1 (the rate is highly persistent).
- `iur` — **insured unemployment rate** (`iur_sa`) averaged over weeks ending in
  M: a direct weekly coincident analogue of the UR. The key signal.
- `claims` — continued + initial SA claims over weeks ending in M.
- `trends` — Google Trends unemployment-search composite (2021+).

Level models target the rate directly; change models target the MoM move and add
back last month's rate. Everything is scored on the **rounded 0.1 level**.

## Backtest verdict (2026-05-29, ~163 origins 2011-01..2026-04, COVID-masked)

In the test window the UR **moved (|Δ|≥0.1) 73% of months**; mean|Δ| 0.109pp. So
persistence is exact only ~1/4 of the time (when the rate holds), and the task is
to call the small moves.

Ranked by exact% (then MAE in pp):

| method | exact% | ≤0.1pp | MAE | n |
|---|--:|--:|--:|--:|
| chg: mom+iur+claims+trends | 35 | 59 | 0.094 | 17* |
| **chg: mom+iur+claims** | **31** | 56 | 0.102 | 163 |
| chg: iur | 30 | 53 | 0.107 | 166 |
| level: mom | 29 | 52 | 0.115 | 163 |
| chg: mom+iur | 29 | 55 | 0.105 | 163 |
| level: mom+iur+claims | 28 | 56 | 0.103 | 163 |
| baseline: rw (persistence) | 26 | 74 | 0.117 | 167 |
| baseline: rw_drift | 25 | 59 | 0.131 | 167 |
| level: mom+iur | 25 | 55 | 0.107 | 163 |

\* trends variant is only 17 origins (2021+) — too few to trust.

Findings:
- **Best robust model: `chg: mom+iur+claims` — 31% exact** vs 26% for
  persistence, with MAE 0.102 vs 0.117 and **RMSE 0.130 vs 0.202** (a ~36% RMSE
  cut). It calls the *direction* of an actual move 47% of the time vs 0% for RW
  (which never predicts a move). IUR + continued claims carry real signal,
  especially for the larger 0.2-0.3pp moves that dominate RMSE.
- **Exact-calling tops out around ~1/3 of months.** Pinpointing a 0.1pp rate one
  month ahead is intrinsically hard: the CPS rate has sampling noise on the order
  of the 0.1 grid itself. ~90% of months land within 0.2pp.
- Persistence (RW) keeps the **highest ≤0.1pp rate (74%)** because it never moves
  away from a correct anchor — but its exact% is low (only right when the rate
  holds) and its RMSE is much worse. The change model trades a few near-misses
  for catching real moves; net it is better on exact%, MAE, and RMSE.
- The change framing slightly beats the level framing throughout.

### BigQuery-native ML survey (`timesfm_bench.py`, `bqml_bench.py`)

Three BQ-native approaches were benchmarked. **None beat the linear change
model** (`chg: mom+iur+claims`, 31% exact / MAE 0.102):

| method | exact% | MAE | notes |
|---|--:|--:|---|
| **Ridge `chg: mom+iur+claims` (winner)** | **31** | 0.102 | linear, IUR+claims features |
| TimesFM 2.5, level (`AI.FORECAST`) | 27 | 0.115 | univariate FM; ~ties RW |
| baseline RW (persistence) | 26 | 0.117 | hard to beat on ≤0.1pp |
| BOOSTED_TREE_REGRESSOR | 25 | 0.117 | overfits at small n |
| ARIMA_PLUS_XREG (matched 2016+) | 16 | 0.237 | unstable — worse than RW |

- **TimesFM** (27% exact) barely edges persistence and trails the feature model —
  a univariate model can't see the IUR / continued-claims coincident signal that
  drives the UR's monthly moves.
- **BOOSTED_TREE** ties persistence — nonlinearity doesn't help at this n.
- **ARIMA_PLUS_XREG** is the worst (MAE 0.237, ~2× RW): auto-ARIMA on the level
  with regressors is unstable for a 0.1-grid rate.

So the linear change model with IUR/claims remains the winner; the lift over
persistence is real but modest, and exact-calling stays ~1/3.

**Live forecast (May-2026, released ~first Friday Jun-2026; last print 4.3%):**
both `mom+iur+claims` framings give ≈ **4.25% → rounds to 4.2%** (model calls a
0.1pp decline).

> Caveat: scored on revised data (only 2 BLS vintages collected so far), so live
> first-print accuracy will be somewhat worse. True first-prints accrue monthly.
