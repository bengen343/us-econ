# ISM Manufacturing PMI forecast

**Target:** the headline ISM Manufacturing PMI for month M, released the 1st
business day of M+1 (10:00 ET). Modelled as the m/m CHANGE with levels
recovered as pmi_{M-1} + ŷ. Job `ism-mfg-forecast` runs daily at 11:00 MT,
writing level + change to `ism.forecast_manufacturing_pmi` (+`_current`
view); the window self-gates — it idles until the month-M S&P flash lands
(~the 21st-24th), then re-nowcasts daily as the late Fed surveys arrive.

## Winning model (`flash_fed_v1`)

Walk-forward expanding-window OLS of ΔPMI on [1, S&P-flash gap
(flash_M − pmi_{M-1}), Fed-composite gap (equal-weight mean of
Empire/Philly/Richmond/Dallas mapped to 50 + raw/2, minus pmi_{M-1}), own
lag]. MIN_TRAIN_SHORT=60 (the flash only starts 2012).

Backtest (COVID 2020-03..2021-06 masked), errors in PMI points:

| window | method | MAE | RMSE | vs random walk |
|---|---|---|---|---|
| 2018+ (all specs) | **flash_fed** | **0.85** | **1.07** | 1.20 (−11%) |
| 2018+ | fed4 (no flash) | 0.86 | 1.11 | — |
| 2010+ (deep history) | fed4 | 0.97 | 1.25 | 1.32 (−5%) |
| 2010+ | ar1 | 1.03 | 1.36 | — |

Losers: **Chicago PMI hurt in every recent combo** (RMSE 1.40-1.41 —
conveniently, since its source is subscriber-gated MNI), the new-orders
lead, mean reversion toward 50, single regional surveys, LightGBM.
Directional accuracy is mediocre (~54-60%) — the ISM is a hard target and
these are honest but modest edges, consistent with the services-ISM
research's "AR(1) is strong" frontier.

Backdated validation: hiding Dec-2025 and nowcasting from its flash + Fed
surveys gave 48.7 vs the actual 47.9 (0.8pt, ≈ the backtest MAE).

## Data flow

Production reads BigQuery only: `ism.report_on_business` (the target;
1948+), `fed_surveys.manufacturing_surveys` (the 4-bank collector built for
this forecast — Empire/Philly/Richmond/Dallas headline SA diffusion indexes
from each bank's official history file), and `ism.sp_global_us_pmi`
(manufacturing flash — the existing collector already parsed it from the
flash PDF; the 2013-2025 history was backfilled from the Sism.xlsm Markit
tab on 2026-06-10). The Fed composite uses whichever banks have reported
(mean-of-available), so mid-window nowcasts run on partial composites and
firm up by the last Monday.

## Caveats / revisit

- **Manufacturing flash gap 2026-01..2026-05**: the workbook backfill ends
  2025-12 and those flashes exist only in their own PDFs — ~5 permanently
  thin training months (live capture covers 2026-06 onward). Fillable by
  PDF hunt if ever worth it.
- Flash-era backtest is short (n≈70 scored months) and showed +0.3 bias
  (over-predicting in the 2022-25 manufacturing slump); the long-window
  fed4 evidence (−5% vs RW over 147 months) is the deeper support.
- The Fed surveys re-estimate seasonal factors annually (history restates);
  latest-vintage backtest, vintages accrue in the collector.
- ISM Services remains a separate pending initiative
  (memory: project_services_ism_forecast) — its harness can now reuse this
  package's pattern plus same-month ISM Manufacturing as a feature.
