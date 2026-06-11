# New home sales forecast

**Target:** new single-family houses sold (SAAR, thousands) for month M —
the headline of the Census/HUD New Residential Sales release, ~the 23rd-27th
of M+1 (10:00 ET). This is the noisiest housing print: the 90% CI on the
published m/m change is ~±12pp and the preliminary SA estimate revises ~5%
on average. Job `census-home-sales-forecast` runs daily at 11:30 MT, writing
SAAR level + m/m % to `census_construction.forecast_new_home_sales`
(+`_current` view); the window self-gates — it idles until the same-month
NRC release lands (~the 17th of M+1) and re-nowcasts through the sales
release.

## Winning model (`perm_both_v1`)

Walk-forward expanding-window OLS of Δlog(sales) on [1, Δlog SF permits for
the SAME month M, the log(SF permits_M / sales_{M-1}) level gap, the own
lag]. MIN_TRAIN=120. The structural story is the cleanest in the repo: the
sales sample is literally drawn from building permits, and the NRC release
publishes month-M SF permits a week before this print.

Backtest (2010+, COVID 2020-03..2021-06 masked):

| method | RMSE 2010+ | RMSE 2017+ | lvl MAE | dir% |
|---|---|---|---|---|
| kitchen (+mortgage/supply/HMI, 7 regressors) | 6.77 | 6.78 | 29k | 65 |
| **perm_both** (production) | **6.93** | **7.03** | 29k | **69-72** |
| perm_gap | 7.00 | 7.25 | 29k | 67-69 |
| ar1 | 7.65 | 7.52 | 32k | 57-65 |
| carry-forward (zero) | 8.03 | 7.97 | 34k | — |

The kitchen sink shaves ~0.2 RMSE but would require a new Freddie-PMMS
mortgage collector and carries overfit risk on this series; mortgage, HMI
(including its SF-sales-present component and the M+1 leading print),
months' supply, and SF starts each added little alone; LightGBM middled.
Direction (~70%) is the spec's strongest suit.

Backdated validation: hiding April 2026 and nowcasting from April SF permits
gave 650k vs the actual 622k (28k ≈ the level MAE).

## Data flow

Production reads BigQuery only, both tables in the shared
`census_construction` dataset: `new_residential_sales` (the
`census_home_sales` collector built for this forecast — sold + for-sale +
published months' supply, US + regions, NSA + SA, vintage-stamped) and
`new_residential_construction` (SF permits, from the starts/permits
initiative). The harness pulls the same census.gov workbooks directly, plus
NAHB/Freddie files for the losing candidates.

## Caveats / revisit

- **The ~5% average preliminary revision makes this the most
  first-print-optimistic backtest in the repo** — latest-vintage history is
  meaningfully smoother than the prints we'll be scored against. First
  prints accrue via the vintage-stamped collector; re-score once a year of
  vintages exists.
- A 29k level MAE on a ~620k base (~4.7%) is close to the print's own
  sampling noise — treat point calls accordingly; the m/m direction call is
  the more reliable output.
- The kitchen sink's mortgage term is the first upgrade candidate if a
  Freddie-PMMS collector ever gets built for another forecast (it nearly
  earned one twice now — starts and here).
- Months' supply is collected (published series, not derived) but unused by
  the winner — available for regime-conditioning experiments.
