# ADP headline — Pulse-bridge forecast (production)

Forecasts the **next unreleased ADP national-monthly headline** (private SA
employment change) and **revises it every weekly NER Pulse release**. Runs as a
Cloud Run Job (`forecast-adp-headline-pulse`) Tuesdays at 07:30 MT, after the
Pulse collector lands the freshest vintage.

## Method

The monthly headline is `ner_sa(M) − ner_sa(M−1)`. The single most predictive
signal (per `../research/`) is the weekly **NER Pulse** — a 4-wk MA of net weekly
SA private-employment change, built from the same payroll panel. We bridge it to
the monthly figure and blend with a random-walk prior by how complete the target
month is:

```
run_rate(M)   = mean Pulse 4wk-MA over weeks ending in M
implied(M)    = run_rate(M) × expected_weeks(M)          # Saturdays in M
b             = shrunk pooled ratio  headline/implied  over complete months
pulse(M)      = b × implied(M)
prior(M)      = last released headline                    # RW
w             = observed_weeks / expected_weeks           # completeness
forecast(M)   = w × pulse(M) + (1 − w) × prior(M)
```

Early in month M (no Pulse weeks yet) the forecast is the prior; as each Tuesday
adds weeks, `w` rises and it migrates to the calibrated Pulse nowcast. All knobs
live in `config.py`; bumping any is a deliberate `MODEL_VERSION` change.

## Files

| File | Role |
|---|---|
| `config.py` | constants: calibration shrinkage, prior, blend, table names |
| `data.py` | read-only, vintage-/as-of-aware BigQuery pulls |
| `model.py` | pure forecast logic → `Forecast` dataclass |
| `main.py` / `__main__.py` | Cloud Run Job entrypoint (self-bootstraps table + view, upserts a revision row; `DRY_RUN=1` to compute-only) |
| `backtest.py` | read-only validation: LOO accuracy, revision trajectory, live forecast |
| `01_forecast_table.sql` | output table + `_current` view DDL (job creates these automatically; file is for docs/manual setup) |

## Output

`adp_employment.forecast_national_monthly` (append-only) + `_current` view
(latest generation per `target_month`). Each row records the blended forecast and
every component, keyed by `(target_month, as_of_pulse_week, model_version)` so the
full weekly-revision trajectory is preserved. The upsert is idempotent per
refresh; a new Pulse week adds a new revision row.

## Run locally (read-only / dry)

```powershell
# Validation report (no writes):
.\.venv\Scripts\python.exe -m forecasts.adp_employment.national_monthly.pulse_bridge.backtest

# Entrypoint, compute + log only (no writes):
$env:GCP_PROJECT="us-econ-51920"; $env:RAW_BUCKET="us-econ-51920-raw"; $env:DRY_RUN="1"
.\.venv\Scripts\python.exe -m forecasts.adp_employment.national_monthly.pulse_bridge
```

Per repo convention the job writes BigQuery only when deployed (push to `main`
→ Cloud Build → Cloud Run); local runs use `DRY_RUN`.

## Honest status (2026-05)

LOO full-month bridge MAE ≈ **25k** (vs ~70k for monthly-feature models, vs the
±12.5k stretch goal) on only 3 complete-Pulse months. The calibration scale and
accuracy will firm up as Pulse history and true first-print vintages accrue.
Direction and revision behaviour are working as designed (see `backtest.py`).
