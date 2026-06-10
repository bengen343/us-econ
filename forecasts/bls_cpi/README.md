# CPI forecasts

Six forecast packages share this directory: the **headline/core nowcast**
(`dms/` research → `production/`) and five **sub-series forecasts**, each in
its own package with the same layout (`harness.py` research bake-off,
`model.py` shared spec, `production/` Cloud Run job). Each package has its own
README with the full bake-off; this page is the map.

All six run on the same cadence — daily on days 1-15 at 05:00 MT (gated in
code to day ≤ 18), re-nowcasting the next-to-be-released month through the
pre-release window — and write append-only tables with `_current` views,
one row per (target, target_month, as_of_date, model_version).

| package | target | model | vs baseline | job / output table |
|---|---|---|---|---|
| `dms/` + `production/` | headline & core, m/m + y/y | `dms_v2` — deterministic Cleveland-Fed bottom-up reconstruction + Manheim used-cars adjustment | beat Ridge, U-MIDAS, DFM | `bls-cpi-forecast` → `forecast_cpi` |
| `gasoline/` | `CUSR0000SETB01` (SA) | `eia_wedge_v1` — month-M complete EIA retail change + calendar wedge | −65% RMSE vs RW | `bls-gasoline-forecast` → `forecast_gasoline` |
| `shelter/` | `CUSR0000SAH1` (SA) | `trail6_v1` — deterministic trailing-6 mean | −16% RMSE vs carry-forward; beat all ZORI specs | `bls-shelter-forecast` → `forecast_shelter` |
| `eggs/` | `APU0000708111` ($/dozen) | `ppi_dl3_seas_v1` — AR + PPI-eggs lags 1-3 + seasonal | −18% RMSE vs RW | `bls-eggs-forecast` → `forecast_eggs` |
| `electricity/` | `APU000072610` ($/kWh) | `seasonal_ar12_v1` — own lags 1/12 + seasonal | −43% RMSE vs RW | `bls-electricity-forecast` → `forecast_electricity` |
| `airfares/` | `CUSR0000SETG01` (SA) | `ar2_wti_v1` — AR(2) + WTI lags 0-2 | −22% RMSE vs carry-forward | `bls-airfares-forecast` → `forecast_airfares` |

## The pattern that emerged

Each market's economics picked its model — worth remembering when adding the
next sub-series:

- **Fast pass-through, observable upstream** (gasoline): the contemporaneous
  month's own input data, nearly deterministic.
- **Lagged pass-through** (eggs ← wholesale): distributed lags of the upstream
  price, timed to the release calendar (PPI M-1 lands before CPI M).
- **Administered / persistent** (shelter, electricity): own history wins;
  exogenous "leading" indicators are already priced into the trailing trend at
  h=1. Deterministic or near-deterministic specs beat fitted ones.
- **Volatile + mean-reverting** (airfares): AR structure first, slow fuel
  pass-through second.

Shared conventions: walk-forward expanding-window OLS on Δlog, COVID masking
(2020-03..2021-06), latest-vintage backtests (SA series re-seasonalised
annually → mildly optimistic vs first prints), `min_periods` tolerance for the
Oct-2025 appropriations-lapse gap, complete-month guards on
daily/weekly-aggregated regressors.
