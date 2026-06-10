# CPI shelter forecast

**Target:** `CUSR0000SAH1` — CPI shelter index, SA. Nowcast of the next print
(h=1, SA level + m/m %), forecast just before the mid-(M+1) CPI release.

## Why this design

Shelter is the *opposite* problem from gasoline: no contemporaneous
high-frequency input exists, and the series is the most persistent CPI
component (long leases + the CPI's 6-month sampling window smooth everything).
The literature is unanimous that market rents lead CPI shelter by **8-14
months** (Richmond Fed; NBER w34113; Boston Fed's model uses ZORI at lag 6 and
house prices at lag 16) — which means at h=1 their signal is already embodied
in shelter's own trailing trend. The bake-off tested that proposition
directly and confirmed it.

## Bake-off (harness.py — two windows, COVID-masked)

Full window (2010-01..2026-04, ~175 origins; ZORI specs excluded — ZORI starts
2015). m/m errors in pct, levels in index points.

| method | mm_MAE | mm_RMSE | lvl_MAE | notes |
|---|--:|--:|--:|---|
| **trail6 (winner)** | **0.067** | **0.092** | **0.218** | deterministic trailing-6 mean |
| trail3 | 0.066 | 0.091 | 0.211 | statistically tied; noisier |
| comp_trail (OLS) | 0.068 | 0.094 | 0.216 | + OER/rent components: no gain |
| ar_trail (OLS) | 0.070 | 0.098 | 0.227 | fitting adds noise |
| baseline: rw_mm | 0.079 | 0.110 | 0.256 | carry forward last m/m |
| baseline: ar1 | 0.088 | 0.123 | 0.282 | |

ZORI window (2021-07+, all specs, MIN_TRAIN=60): trailing means still win;
the best market-rent spec (`zori_gap` — trailing-12 ZORI growth minus
trailing-12 shelter growth) is competitive on MAE but worse on RMSE, and ZORI
lags 1/6/12 are strictly worse. trail6 vs trail3 is a coin flip; trail6 won
the clean common-month comparison on both windows and is smoother.

The BLS New Tenant Rent index was also considered and excluded: quarterly,
6-12+ month lead, and publication is paused (2026-04) so production could not
consume it anyway. This independently re-confirms the repo's earlier decision
to leave ZORI/NTR out of the headline CPI forecast at this horizon.

## Production (`production/`, model `trail6_v1`)

**Deterministic — no fitting** (mirroring the dms treatment of persistent
components): trailing 6-month mean of Δlog(SA m/m), chained onto the last
published level. Tolerates ≥4 of 6 trailing months (`MIN_TRAIL`): the Oct-2025
appropriations lapse left two m/m changes unpublished, and partial windows are
seasonally safe on an SA series (same `min_periods` convention as dms). Input:
`bls_cpi.cpi_series` only. Job `bls-shelter-forecast`, daily days 1-15
05:00 MT, writes `bls_cpi.forecast_shelter` (+`_current`), targets
`shelter_cpi_level` / `shelter_cpi_mm`.

## Live scoring & revisit

- **2026-05 print (first live test): near-exact.** Forecast 428.10 (+0.34%
  m/m); actual 428.00 (+0.32%). Error +0.10 points / +0.02pp.
- **Multi-month horizons are where ZORI would matter.** If shelter forecasts
  at h=3-12 are ever wanted, start from the harness's `zori_gap` spec — the
  market-vs-CPI catch-up term is the literature's convergence mechanism and
  was already the best exogenous spec at h=1.
- Revisit trail3-vs-trail6 once shelter decelerations steepen: a shorter mean
  adapts faster around turning points, and the two were within noise of each
  other.
