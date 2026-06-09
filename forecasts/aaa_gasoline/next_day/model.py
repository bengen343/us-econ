"""Error-correction models + walk-forward + Diebold-Mariano for retail gasoline.

The forecast quantity is the one-step-ahead change in the retail price level,
Delta r_{t+1} = r_{t+1} - r_t, added back to r_t for a level forecast. Retail and
the wholesale RBOB benchmark are cointegrated (slope ~1 plus a markup/tax wedge),
so the workhorse is an error-correction model:

  Delta r_{t+1} = mu + sum_i phi_i * Delta x_{t-i}        (distributed-lag pass-through)
                     + (psi_j * Delta r_{t-j})            (retail momentum)
                     + rho * EC_t                         (equilibrium correction)
                     + e

where x is RBOB, EC_t = r_t - (a + b*x_t) is the prior-period disequilibrium from
the long-run cointegrating regression. The asymmetric ("rockets and feathers")
variant splits EC_t and the contemporaneous Delta x_t into positive/negative
parts so upward and downward adjustment can differ.

All estimation is plain OLS (numpy). Cointegration (a, b) is re-estimated on the
expanding training window at every step, so the walk-forward is leakage-free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.stats import t as t_dist


@dataclass(frozen=True)
class Spec:
    """A short-run ECM specification over base series r (retail) and x (RBOB)."""

    name: str
    dx_lags: tuple[int, ...] = (0, 1, 2, 3)  # lags of Delta x (RBOB)
    dr_lags: tuple[int, ...] = (1,)  # lags of Delta r (retail momentum)
    dwti_lags: tuple[int, ...] = ()  # lags of Delta wti (crude), optional
    ec_mode: str = "sym"  # "none" | "sym" | "asym"
    asym_dx0: bool = False  # split contemporaneous Delta x into +/-
    deseason_ec: bool = False  # subtract per-calendar-month mean from EC
    label: str = ""

    @property
    def max_lag(self) -> int:
        return max((*self.dx_lags, *self.dr_lags, *self.dwti_lags, 1))


# The model menu compared in the research harness.
SPECS: list[Spec] = [
    Spec("ar1", dx_lags=(), dr_lags=(1,), ec_mode="none", label="AR(1) on d_retail"),
    Spec("ecm_sym", ec_mode="sym", label="Symmetric ECM (RBOB)"),
    Spec(
        "ecm_asym",
        ec_mode="asym",
        asym_dx0=True,
        label="Asymmetric ECM (RBOB, rockets&feathers)",
    ),
    Spec("ecm_sym_wti", dwti_lags=(0, 1), ec_mode="sym", label="Symmetric ECM + WTI lags"),
    Spec(
        "ecm_sym_seas",
        ec_mode="sym",
        deseason_ec=True,
        label="Symmetric ECM (RBOB, seasonal EC)",
    ),
]


def long_run(retail: np.ndarray, rbob: np.ndarray) -> tuple[float, float]:
    """OLS cointegrating regression retail = a + b*rbob; returns (a, b)."""
    X = np.column_stack([np.ones(len(rbob)), rbob])
    beta, *_ = np.linalg.lstsq(X, retail, rcond=None)
    return float(beta[0]), float(beta[1])


def seasonal_resid_means(resid: np.ndarray, months: np.ndarray) -> dict[int, float]:
    """Mean cointegration residual per calendar month.

    The retail-RBOB wedge is seasonal (~20c/gal calendar swing, widest around the
    Sep RVP winter-grade futures transition), so the raw EC carries a predictable
    seasonal component that reads as spurious disequilibrium. Subtracting these
    means re-centers EC on the month's own normal wedge.
    """
    return {m: float(resid[months == m].mean()) for m in range(1, 13) if (months == m).any()}


def _lag(arr: np.ndarray, pos: np.ndarray, lag: int) -> np.ndarray:
    """Value of `arr` at positions `pos - lag` (NaN where out of range)."""
    out = np.full(len(pos), np.nan)
    src = pos - lag
    ok = src >= 0
    out[ok] = arr[src[ok]]
    return out


def _design(
    spec: Spec,
    pos: np.ndarray,
    dr: np.ndarray,
    dx: np.ndarray,
    dwti: np.ndarray,
    ec: np.ndarray,
) -> np.ndarray:
    """Build the OLS design matrix (with intercept) for rows `pos`."""
    cols: list[np.ndarray] = [np.ones(len(pos))]
    for lag in spec.dx_lags:
        if spec.asym_dx0 and lag == 0:
            continue  # replaced by the +/- split below
        cols.append(_lag(dx, pos, lag))
    for lag in spec.dr_lags:
        cols.append(_lag(dr, pos, lag))
    for lag in spec.dwti_lags:
        cols.append(_lag(dwti, pos, lag))
    if spec.ec_mode == "sym":
        cols.append(_lag(ec, pos, 0))
    elif spec.ec_mode == "asym":
        e = _lag(ec, pos, 0)
        cols.append(np.clip(e, 0, None))  # EC+ : retail above equilibrium
        cols.append(np.clip(e, None, 0))  # EC- : retail below equilibrium
    if spec.asym_dx0:
        d0 = _lag(dx, pos, 0)
        cols.append(np.clip(d0, 0, None))
        cols.append(np.clip(d0, None, 0))
    return np.column_stack(cols)


def design_columns(spec: Spec) -> list[str]:
    """Column names matching `_design`, in order (for the named live fit)."""
    names = ["const"]
    for lag in spec.dx_lags:
        if spec.asym_dx0 and lag == 0:
            continue
        names.append(f"dx{lag}")
    names += [f"dr{lag}" for lag in spec.dr_lags]
    names += [f"dwti{lag}" for lag in spec.dwti_lags]
    if spec.ec_mode == "sym":
        names.append("ec")
    elif spec.ec_mode == "asym":
        names += ["ec_pos", "ec_neg"]
    if spec.asym_dx0:
        names += ["dx0_pos", "dx0_neg"]
    return names


def fit_full(panel: pd.DataFrame, spec: Spec) -> tuple[float, float, dict[str, float]]:
    """Fit `spec` on the entire panel. Returns (a, b, {coef_name: value}).

    Used for the live forecast so it applies the very same model form the
    research walk-forward evaluates.
    """
    r = panel["retail"].to_numpy()
    x = panel["rbob"].to_numpy()
    wti = panel["wti"].to_numpy() if "wti" in panel else np.full(len(r), np.nan)
    dr = np.concatenate([[np.nan], np.diff(r)])
    dx = np.concatenate([[np.nan], np.diff(x)])
    dwti = np.concatenate([[np.nan], np.diff(wti)])
    n = len(r)
    a, b = long_run(r, x)
    ec = r - (a + b * x)
    if spec.deseason_ec:
        months = panel.index.month.to_numpy()
        seas = seasonal_resid_means(ec, months)
        ec = ec - np.array([seas[m] for m in months])
    warmup = spec.max_lag + 1
    pos = np.arange(warmup, n - 1)
    X = _design(spec, pos, dr, dx, dwti, ec)
    y = dr[pos + 1]
    keep = np.isfinite(X).all(axis=1) & np.isfinite(y)
    beta, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
    return a, b, dict(zip(design_columns(spec), beta, strict=True))


@dataclass(frozen=True)
class NextDay:
    """A live next-day retail forecast and its supporting diagnostics."""

    a: float  # long-run intercept (markup/tax wedge)
    b: float  # long-run RBOB slope
    equilibrium: float  # RBOB-implied retail level = a + b*rbob
    ec: float  # disequilibrium: anchor - equilibrium (>0 = retail dear)
    weekly_move: float  # expected one-week retail change ($/gal)
    next_day: float  # next-day forecast level ($/gal)
    n_train: int


def next_day_forecast(
    panel: pd.DataFrame,
    spec: Spec,
    anchor: float,
    rbob: float,
    days_per_week: float = 5.0,
    as_of_month: int | None = None,
) -> NextDay:
    """Live next-day level forecast: fit `spec` on the full weekly `panel`, then
    evaluate the expected weekly move at the live state (latest weekly RBOB
    changes + the disequilibrium of `anchor` vs the RBOB-implied equilibrium) and
    take ~1/`days_per_week` of it as the one-day change off `anchor`.

    `anchor` is the latest retail level being forecast forward (the AAA price in
    production, or any retail level), `rbob` the latest RBOB level. For a
    deseasonalized spec the equilibrium includes the calendar month's normal
    wedge (`as_of_month`, defaulting to the panel's last month), so EC measures
    only the abnormal part of the gap.
    """
    a, b, coef = fit_full(panel, spec)
    equilibrium = a + b * rbob
    if spec.deseason_ec:
        months = panel.index.month.to_numpy()
        resid = panel["retail"].to_numpy() - (a + b * panel["rbob"].to_numpy())
        seas = seasonal_resid_means(resid, months)
        month = as_of_month if as_of_month is not None else int(panel.index[-1].month)
        equilibrium += seas.get(month, 0.0)
    ec = anchor - equilibrium

    dx_week = panel["rbob"].diff()
    dr_week = panel["retail"].diff()

    dhat = coef["const"] + coef.get("dr1", 0.0) * float(dr_week.iloc[-1])
    for lag in spec.dx_lags:
        if spec.asym_dx0 and lag == 0:
            continue  # carried by the +/- split below
        dhat += coef.get(f"dx{lag}", 0.0) * float(dx_week.iloc[-1 - lag])
    if spec.asym_dx0:
        d0 = float(dx_week.iloc[-1])
        dhat += coef.get("dx0_pos", 0.0) * max(d0, 0.0) + coef.get("dx0_neg", 0.0) * min(d0, 0.0)
    if spec.ec_mode == "sym":
        dhat += coef.get("ec", 0.0) * ec
    elif spec.ec_mode == "asym":
        dhat += coef.get("ec_pos", 0.0) * max(ec, 0.0) + coef.get("ec_neg", 0.0) * min(ec, 0.0)

    return NextDay(
        a=a,
        b=b,
        equilibrium=equilibrium,
        ec=ec,
        weekly_move=dhat,
        next_day=anchor + dhat / days_per_week,
        n_train=len(panel),
    )


@dataclass(frozen=True)
class DistBucket:
    """One half-cent (or `width`-wide) probability band of the next-day price."""

    low: float
    high: float
    mid: float
    prob: float


def forecast_error_sigma(
    panel: pd.DataFrame,
    spec: Spec,
    test_start: pd.Timestamp,
    days_per_week: float = 5.0,
) -> tuple[float, float]:
    """(weekly_sigma, daily_sigma) from the out-of-sample one-step weekly errors.

    The honest forecast-uncertainty estimate is the spread of the walk-forward
    residuals (not in-sample). The weekly error variance accumulates ~linearly
    from daily innovations, so the one-day-ahead sigma is the weekly sigma divided
    by sqrt(days_per_week) -- corroborated by the (tiny-n) AAA daily backtest.
    """
    preds = walk_forward(panel, spec, test_start)
    err = (preds - panel["retail"]).dropna()
    weekly_sigma = float(err.std(ddof=1))
    return weekly_sigma, weekly_sigma / math.sqrt(days_per_week)


def predictive_distribution(
    point: float,
    sigma: float,
    width: float = 0.005,
    span_sigmas: float = 4.0,
) -> list[DistBucket]:
    """Gaussian predictive distribution centered at `point`, discretized into
    `width`-wide bands (default 0.5 cent) snapped to a clean `width` grid and
    spanning +/- `span_sigmas`. Bucket prob = Phi(high) - Phi(low); at 4 sigma the
    mass outside the grid is ~0.006%, so the bands effectively sum to 1."""
    if sigma <= 0:
        return []
    lo = math.floor((point - span_sigmas * sigma) / width) * width
    hi = math.ceil((point + span_sigmas * sigma) / width) * width
    n = int(round((hi - lo) / width))
    edges = [round(lo + i * width, 6) for i in range(n + 1)]
    cdf = norm.cdf(edges, loc=point, scale=sigma)
    return [
        DistBucket(
            low=edges[i],
            high=edges[i + 1],
            mid=round((edges[i] + edges[i + 1]) / 2, 6),
            prob=float(cdf[i + 1] - cdf[i]),
        )
        for i in range(n)
    ]


def walk_forward(panel: pd.DataFrame, spec: Spec, test_start: pd.Timestamp) -> pd.Series:
    """Expanding-window one-step-ahead level forecasts for `spec`.

    `panel` is weekly with columns retail, rbob, wti. Returns a Series of
    predicted retail LEVELS indexed by the date being forecast (t+1), over the
    test window.
    """
    r = panel["retail"].to_numpy()
    x = panel["rbob"].to_numpy()
    wti = panel["wti"].to_numpy() if "wti" in panel else np.full(len(r), np.nan)
    dr = np.concatenate([[np.nan], np.diff(r)])
    dx = np.concatenate([[np.nan], np.diff(x)])
    dwti = np.concatenate([[np.nan], np.diff(wti)])
    idx = panel.index
    months = idx.month.to_numpy()
    n = len(r)

    warmup = spec.max_lag + 1
    preds = pd.Series(np.nan, index=idx, dtype=float)
    start_pos = max(int(np.searchsorted(idx, test_start)), warmup + 30)

    for p in range(start_pos, n - 1):
        a, b = long_run(r[: p + 1], x[: p + 1])
        ec = r - (a + b * x)  # known through position p
        if spec.deseason_ec:
            # PIT seasonal centering: each month's mean uses only obs <= p, and
            # months with too few observations stay NaN (their rows drop out).
            resid, ec = ec, np.full(n, np.nan)
            for m in range(1, 13):
                mask = months == m
                hist = mask & (np.arange(n) <= p)
                if hist.sum() >= 3:
                    ec[mask] = resid[mask] - resid[hist].mean()
        # Training pairs s -> target dr[s+1], for s in [warmup, p-1].
        train_pos = np.arange(warmup, p)
        X = _design(spec, train_pos, dr, dx, dwti, ec)
        y = dr[train_pos + 1]
        keep = np.isfinite(X).all(axis=1) & np.isfinite(y)
        if keep.sum() < X.shape[1] + 5:
            continue
        beta, *_ = np.linalg.lstsq(X[keep], y[keep], rcond=None)
        x_pred = _design(spec, np.array([p]), dr, dx, dwti, ec)
        if not np.isfinite(x_pred).all():
            continue
        dr_hat = float((x_pred @ beta)[0])
        preds.iloc[p + 1] = r[p] + dr_hat
    return preds


def random_walk(panel: pd.DataFrame, test_start: pd.Timestamp) -> pd.Series:
    """RW level forecast: r_hat_{t+1} = r_t (no change)."""
    r = panel["retail"]
    pred = r.shift(1)
    return pred[pred.index >= test_start]


def score(actual: pd.Series, pred: pd.Series) -> dict[str, float]:
    d = pd.concat([actual.rename("a"), pred.rename("p")], axis=1).dropna()
    err = (d["p"] - d["a"]).to_numpy()
    ae = np.abs(err)
    return {
        "n": float(len(err)),
        "MAE": float(np.mean(ae)) if len(err) else np.nan,
        "RMSE": float(np.sqrt(np.mean(err**2))) if len(err) else np.nan,
        "bias": float(np.mean(err)) if len(err) else np.nan,
    }


def dm_test(actual: pd.Series, pred_a: pd.Series, pred_b: pd.Series, power: int = 2) -> dict:
    """Diebold-Mariano (h=1) of model A vs B. Negative stat favors A (lower loss).

    Uses squared-error loss and the Harvey-Leybourne-Newbold small-sample
    correction (for h=1 the only adjustment is the Student-t reference with n-1
    df; there is no serial-correlation term to correct at one step ahead).
    """
    d = pd.concat([actual.rename("y"), pred_a.rename("a"), pred_b.rename("b")], axis=1).dropna()
    ea = (d["a"] - d["y"]).to_numpy()
    eb = (d["b"] - d["y"]).to_numpy()
    loss = np.abs(ea) ** power - np.abs(eb) ** power
    n = len(loss)
    if n < 8:
        return {"dm": np.nan, "p": np.nan, "n": n}
    dbar = loss.mean()
    var = loss.var(ddof=1) / n
    if var <= 0:
        return {"dm": np.nan, "p": np.nan, "n": n}
    dm = dbar / np.sqrt(var)
    p = 2 * (1 - t_dist.cdf(abs(dm), df=n - 1))
    return {"dm": float(dm), "p": float(p), "n": n}
