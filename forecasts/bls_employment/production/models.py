"""Live forecasts for the upcoming Employment Situation, for every production model.

Each model trains on all observed (non-COVID) history and predicts the live
(not-yet-released) month — the panels carry that month as a trailing row with the
target NaN but the coincident features (claims, ISM, Conference Board, ...)
populated as they land through the release week. Reuses the research feature
builders so production and backtest stay in lockstep.

Roster (see ../README files for the backtest evidence):
  NFP headline (MoM change, thousands):
    ridge_mom_claims  - the backtest winner (~82k MAE)
    ridge_surveys     - + ISM Mfg/Svc employment & PMI, CB labor differential
  Unemployment rate (level, percent; modelled as the MoM change, +last level):
    ridge_mom_iur_claims - the winner (~31% exact / 0.10pp MAE)
    ridge_surveys        - + ISM + CB surveys
    umidas               - U-MIDAS: weekly IUR + continued claims at native freq
    dfm                  - dynamic factor model (Kalman nowcast of the UR change)
  Plus an `ensemble` per target = mean of that target's models.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from statsmodels.tsa.statespace.dynamic_factor_mq import DynamicFactorMQ

from forecasts.bls_employment import data, dfm, midas, multivariate
from forecasts.bls_employment.payrolls_headline import panel as nfp_panel
from forecasts.bls_employment.production import config as cfg
from forecasts.bls_employment.unemployment_rate import panel as ur_panel

_MIN_TRAIN = 36

_log = logging.getLogger(__name__)


def _warn_skipped(target: str, model_version: str, panel: pd.DataFrame, cols: list[str]) -> None:
    """A model silently missing from the output is a roster failure worth flagging:
    name the live features that are NaN (usually a collector gap)."""
    live = panel.loc[panel.index.max(), cols]
    missing = sorted(live.index[live.isna()])
    _log.warning(
        "production model skipped",
        extra={
            "extras": {
                "target": target,
                "model_version": model_version,
                "live_month": str(panel.index.max().date()),
                "missing_live_features": missing,
                "reason": "missing live features" if missing else f"n_train < {_MIN_TRAIN}",
            }
        },
    )


@dataclass
class ForecastRow:
    target: str
    model_version: str
    target_month: pd.Timestamp
    value: float  # NFP: MoM change (k). UR: rate level (%).
    value_rounded: float
    units: str
    n_train: int


def _ridge_live(panel: pd.DataFrame, cols: list[str], target_col: str):
    """Fit RidgeCV on non-COVID observed rows; predict the live (last) row.

    Returns (pred, n_train) or None if the live features are incomplete.
    """
    obs = panel[(~panel["is_covid"]) & panel[target_col].notna()]
    train = panel.loc[obs.index, [target_col, *cols]].dropna()
    live = panel.index.max()
    x = panel.loc[[live], cols]
    if len(train) < _MIN_TRAIN or x.isna().any(axis=None):
        return None
    model = make_pipeline(StandardScaler(), RidgeCV(alphas=cfg.RIDGE_ALPHAS))
    model.fit(train[cols].values, train[target_col].values)
    return float(model.predict(x.values)[0]), len(train)


def _nfp_row(model_version: str, target_month, change: float, n: int) -> ForecastRow:
    return ForecastRow(
        cfg.TARGET_NFP,
        model_version,
        target_month,
        round(change, 1),
        float(round(change)),
        "thousands",
        n,
    )


def _ur_row(model_version: str, target_month, level: float, n: int) -> ForecastRow:
    return ForecastRow(
        cfg.TARGET_UR,
        model_version,
        target_month,
        round(level, 3),
        float(round(level, 1)),
        "percent",
        n,
    )


def _dfm_ur_live(client) -> tuple[float, int, pd.Timestamp] | None:
    """Dynamic-factor Kalman nowcast of the UR change for the live month."""
    df, ur_level, _ = dfm.build_panel(client)
    masked = dfm._mask_covid(df)
    live = df.index.max()
    last_level = ur_level.get(live - pd.offsets.MonthBegin(1))
    if pd.isna(last_level):
        return None
    params = dfm._fit_params(masked).params
    model = DynamicFactorMQ(
        masked,
        factors=cfg.DFM_FACTORS,
        factor_orders=cfg.DFM_FACTOR_ORDERS,
        idiosyncratic_ar1=True,
        standardize=True,
    )
    pred = model.smooth(params).predict()
    ur_chg = pred.at[live, "ur_chg"]
    if pd.isna(ur_chg):
        return None
    n = int(masked["ur_chg"].notna().sum())
    return float(last_level + ur_chg), n, live


def compute(client) -> list[ForecastRow]:
    warnings.simplefilter("ignore")
    claims = data.pull_claims_national(client)
    trends = data.pull_trends(client)
    ism = data.pull_ism(client)
    cb = data.pull_conference_board(client)

    rows: list[ForecastRow] = []

    # ---- NFP headline ------------------------------------------------------ #
    nfp, ng = nfp_panel.build_panel(
        bls=data.pull_bls_series(nfp_panel.BLS_SERIES, client),
        claims=claims,
        adp=data.pull_adp_monthly(client),
        pulse=data.pull_adp_pulse(client),
        trends=trends,
        challenger=data.pull_challenger(client),
    )
    sg = multivariate._add_surveys(nfp, ism, cb)
    nfp_live = nfp.index.max()
    nfp_specs = {
        "ridge_mom_claims": ng["momentum"] + ng["claims"],
        "ridge_surveys": ng["momentum"] + ng["claims"] + sg["ism"] + sg["cb"],
    }
    nfp_changes = []
    for mv, cols in nfp_specs.items():
        r = _ridge_live(nfp, cols, "y")
        if r is not None:
            rows.append(_nfp_row(mv, nfp_live, r[0], r[1]))
            nfp_changes.append(r[0])
        else:
            _warn_skipped(cfg.TARGET_NFP, mv, nfp, cols)
    if nfp_changes:
        rows.append(_nfp_row("ensemble", nfp_live, float(np.mean(nfp_changes)), len(nfp_changes)))

    # ---- Unemployment rate ------------------------------------------------- #
    ur, ug = ur_panel.build_panel(
        bls=data.pull_bls_series(ur_panel.BLS_SERIES, client), claims=claims, trends=trends
    )
    sgu = multivariate._add_surveys(ur, ism, cb)
    midas_cols = midas._add_lags(ur, claims, ["iur_sa", "claims_continued_sa"])
    ur_live = ur.index.max()
    last_ur = ur.at[ur_live, "ur_lag1"]
    ur_specs = {
        "ridge_mom_iur_claims": ug["momentum"] + ug["iur"] + ug["claims"],
        "ridge_surveys": ug["momentum"] + ug["iur"] + ug["claims"] + sgu["ism"] + sgu["cb"],
        "umidas": ug["momentum"] + midas_cols,
    }
    ur_levels = []
    for mv, cols in ur_specs.items():
        r = _ridge_live(ur, cols, "y_chg")
        if r is not None and pd.notna(last_ur):
            level = last_ur + r[0]
            rows.append(_ur_row(mv, ur_live, level, r[1]))
            ur_levels.append(level)
        else:
            _warn_skipped(cfg.TARGET_UR, mv, ur, cols)

    dfm_r = _dfm_ur_live(client)
    if dfm_r is not None:
        rows.append(_ur_row("dfm", dfm_r[2], dfm_r[0], dfm_r[1]))
        ur_levels.append(dfm_r[0])
    else:
        _log.warning(
            "production model skipped",
            extra={"extras": {"target": cfg.TARGET_UR, "model_version": "dfm"}},
        )

    if ur_levels:
        rows.append(_ur_row("ensemble", ur_live, float(np.mean(ur_levels)), len(ur_levels)))

    return rows
