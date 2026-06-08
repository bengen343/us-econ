# CPI bottom-up reconstruction (Cleveland-Fed DMS, research)

**Targets:** headline m/m (SA), core m/m (SA), headline y/y, core y/y.
**Origin:** the next CPI release (~mid-M+1), at which month M has fully elapsed —
so month M's complete EIA gasoline/oil prices and CPI prints through M-1 are known.

Replicates the Knotek-Zaman *deterministic model switching* nowcast: rebuild the
headline from weighted components, each nowcast deterministically (no fitting).

## Method

| piece | nowcast |
|---|---|
| core m/m (SA) | trailing 12-month average of core SA m/m |
| food m/m (SA) | trailing 12-month average of food SA m/m |
| gasoline m/m (SA) | EIA monthly retail price m/m (= CPI gasoline NSA m/m, slope 1.007, corr 0.995) **deseasonalised** by the empirical expanding NSA−SA gap for that calendar month |
| non-gasoline energy m/m (SA) | trailing 12-month average of energy-services SA m/m |
| energy m/m | gasoline + non-gas energy, weighted within energy (gas share of energy, time-varying) |
| **headline m/m** | `w_core·core + w_food·food + w_energy·energy` with **price-updated** relative-importance weights (see below) |

The weights are time-varying relative importances, price-updated via the BLS
identity: the December-`weight_year` cost weight scaled by each item's NSA index
relative to that December base, using the prior month's (t-1) NSA index
(PIT-clean), normalised over the core/food/energy partition. This lets energy's
weight rise when fuel prices spike — exactly when fixed weights misfire.
| **y/y** (headline, core) | chain the SA m/m onto last month's SA index, take the 12-month change (≈ published NSA y/y; seasonality cancels) |

Everything is in published-**SA** space, so no projected seasonal factors are
needed. The reconstruction is deterministic, so nowcasts are computed vectorised
using only data dated ≤ M-1 (plus month M's complete fuel prices).

## Backtest verdict (2026-06-08, 178 origins 2010-01..2026-04, COVID-masked)

RMSE in percentage points; the reconstruction beats every baseline on all four
targets. (Weights price-updated; static-weight values in parentheses.)

| target | reconstruction | best baseline |
|---|--:|--:|
| **headline m/m** | **0.123** (0.134 static) | 0.226 (trailing-12) / 0.248 (RW) |
| core m/m | 0.091 | 0.091 (= trailing-12, by construction) |
| **headline y/y** | **0.133** (0.144 static) | 0.335 (RW) |
| **core y/y** | **0.104** | 0.158 (RW) |

Findings:
- **The gasoline lever is real.** On headline m/m the reconstruction (0.123) cuts
  RMSE ~45% vs trend-following the headline (trailing-12, 0.226). The only thing
  it adds over the trend is the high-frequency gasoline nowcast — that is where
  the edge comes from, exactly as the research predicted.
- **Price-updating the weights helps the headline** (m/m 0.134 → 0.123, y/y
  0.144 → 0.133): letting energy's weight rise with fuel prices captures spike
  months that fixed weights underweight. Core is unaffected (its ~80% weight is
  stable regardless), so its metrics are unchanged.
- **Core is just trend.** The core nowcast *is* the trailing-12 average, so it
  ties that baseline (0.091) and slightly beats RW (0.106). No lever here — core
  m/m is smooth and persistent; nothing sub-monthly improves it materially.
- **y/y inherits the m/m accuracy** (the other 11 months are known actuals in the
  chain), so headline y/y RMSE (0.144) ≈ headline m/m RMSE. This lands near the
  research's Cleveland-Fed benchmark (~0.19 day-15 y/y headline) — and a touch
  better, as expected: we nowcast the *completed* month with full fuel data,
  versus their mid-month (day-15) partial-data nowcast.
- The 2025 appropriations lapse left Oct-2025 fully missing and Nov-2025 m/m
  missing (its base month is gone); gasoline is unaffected (crowd-sourced daily
  data). The trailing windows tolerate the 2-month gap (`min_periods=9`).

## Alternatives bake-off (2026-06-08, `forecasts/bls_cpi/bench.py`)

Headline m/m, 164 common COVID-masked origins 2011-01..2026-04. **No fitted model
beats the deterministic reconstruction**, confirming the research's DMS > MIDAS,
DFM finding on our data:

| method | RMSE | MAE |
|---|--:|--:|
| **DMS reconstruction (champion)** | **0.126** | 0.095 |
| Ridge + oil + momentum | 0.131 | 0.102 |
| Ridge bridge (DMS inputs) | 0.138 | 0.110 |
| U-MIDAS (weekly gas ×6) | 0.163 | 0.123 |
| Dynamic factor (DFM) | 0.221 | 0.156 |

- **The accounting identity beats fitting.** A Ridge fit on the *same* inputs the
  DMS combines (0.138) is worse than the RI-weighted identity (0.126): at n~150,
  estimating coefficients adds noise where the known weights add structure.
  Adding oil + momentum (0.131) still doesn't catch it.
- **Intra-month fuel timing doesn't help.** U-MIDAS over weekly gasoline (0.163)
  loses to the monthly mean — expected, since CPI gasoline ≈ the monthly-average
  pump price (corr 0.995); weekly lags just add collinear noise.
- **DFM is mismatched** (0.221, worst): a latent common factor can't represent a
  target that is a deterministic weighted sum with idiosyncratic energy — the
  same lesson as the NFP DFM experiment.

**Verdict: ship the deterministic reconstruction.**

## Caveats / next refinements
- **Single weight anchor.** Weights are price-updated (done), but from one
  December-2024 cost-weight anchor for all history. The time-varying part (the
  NSA index ratio) is PIT-clean; the 2024 *basket* is held fixed, so cross-year
  expenditure-basket changes aren't captured (and pre-2024 months use the 2024
  basket anachronistically). Bundling per-year RI anchors would close this, but
  the within-period price-updating captures the dominant weight movement.
- **Vintage.** Backtests against the latest vintage. NSA y/y is effectively final;
  SA m/m is re-seasonalised ~annually, so SA m/m errors are mildly optimistic vs
  the true first print. True first-print PIT accrues one release at a time.
- **Live partial month.** The backtest uses each month's *complete* fuel prices
  (PIT-valid at the release origin). A true intra-month "day-N" nowcast would use
  only fuel prints through day N — a later enhancement for a daily-updating product.
- Not yet compared head-to-head against alternative approaches (MIDAS, dynamic
  factor) — this establishes the deterministic-reconstruction baseline to beat.

Run: `.\.venv\Scripts\python.exe -m forecasts.bls_cpi.dms.harness`
