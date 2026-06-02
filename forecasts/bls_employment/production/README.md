# Employment Situation forecast (production)

Cloud Run Job that forecasts the **about-to-be-released** Employment Situation —
the NFP headline (MoM change) and the unemployment rate (level) — from a small
side-by-side model ensemble, and writes a revising forecast through the release
week. Productionizes the winners + close runners-up from the research harnesses
(`../payrolls_headline`, `../unemployment_rate`, `../midas.py`, `../dfm.py`).

```
.\.venv\Scripts\python.exe -m forecasts.bls_employment.production   # DRY_RUN=1 for local
```

## Model roster (`models.py`)

Each model trains on all non-COVID history each run and nowcasts the live month.

| target | model_version | what |
|---|---|---|
| `nfp_headline` | `ridge_mom_claims` | backtest winner, ~82k MAE |
| | `ridge_surveys` | + ISM Mfg/Svc employment & PMI, CB labor differential |
| | `ensemble` | mean of the above |
| `unemployment_rate` | `ridge_mom_iur_claims` | winner, ~31% exact / 0.10pp MAE |
| | `ridge_surveys` | + ISM + CB surveys |
| | `umidas` | U-MIDAS: weekly IUR + continued claims, native frequency |
| | `dfm` | dynamic factor model (Kalman nowcast of the UR change) |
| | `ensemble` | mean of the above |

A model whose live features aren't in BigQuery yet (e.g. ISM/CB for the current
month before their collectors run) **skips gracefully** and is excluded from the
ensemble that run; it auto-joins once its inputs land. NFP is stored as the MoM
change (thousands); UR as the rate level (percent), with `forecast_rounded` to
0.1pp (the "call it exactly" metric).

## Cadence & output

Fires daily on the first 7 days of the month at 05:00 MT; the job gates in code
to weekdays **on/before the first Friday** (before the 06:30 MT release), so the
forecast for that month firms up as Conference Board → ISM → ADP → claims land.

Output (`main.py`, self-bootstrapped on first run):
- `bls_employment.forecast_employment_situation` — append-only, one row per
  `(target, target_month, as_of_date, model_version)`. Same-day re-runs are
  idempotent (keyed on `as_of_date`); distinct days preserve the revision
  trajectory.
- `..._current` view — latest generation per `(target, target_month,
  model_version)`.

`DRY_RUN=1` computes + logs without writing (local validation; per repo
convention the forecast writes BigQuery only from the deployed Job).

## Deploy

Push to `main` (Cloud Build builds the image) + `terraform apply` (the
`employment_situation_forecast` module in `terraform/forecasts.tf`; the runner SA
already has `bls_employment` dataEditor). `statsmodels` (used by the DFM) is in
the runtime `dependencies` in `pyproject.toml`, so it ships in the image.
