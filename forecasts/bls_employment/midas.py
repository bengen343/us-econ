r"""MIDAS (mixed-frequency) experiment for the NFP and unemployment-rate nowcasts.

The deep-research review flagged that monthly-averaging the weekly claims series
throws away within-month information, and that MIDAS is the canonical fix at the
one-month horizon. This harness tests that directly: instead of one monthly mean
per claims series, it feeds the **K most recent weekly observations at native
weekly frequency** as separate Ridge-regularised features — i.e. *unrestricted*
MIDAS (U-MIDAS; Foroni, Marcellino & Schumacher 2015). Ridge handles the
collinear weekly lags, which is the small-sample-robust alternative to fitting a
restricted Almon/beta lag polynomial.

PIT timing is unchanged: forecasting month M at its release origin, the knowable
weekly values are those ending on/before the last day of M. ``w0`` is the most
recent such week, ``w1`` the week before, etc.

We reuse each target's existing panel + walk_forward_model + scoring, so MIDAS
specs are scored on the SAME COVID-masked origins as the prior monthly specs —
an apples-to-apples test of "does weekly resolution beat the monthly mean?".

Run:  .\.venv\Scripts\python.exe -m forecasts.bls_employment.midas
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import harness as nfp_h
from forecasts.bls_employment.payrolls_headline import panel as nfp_panel
from forecasts.bls_employment.unemployment_rate import harness as ur_h
from forecasts.bls_employment.unemployment_rate import panel as ur_panel

K = 13  # weekly lags per series (~one quarter of weeks; covers within-month + momentum)


def _weekly_lags(weekly: pd.DataFrame, col: str, months: pd.DatetimeIndex, k: int) -> pd.DataFrame:
    """K most recent weekly values of ``col`` with week_ending <= end of month M.

    Column ``{col}_w{j}``: j=0 is the most recent week, j=k-1 the oldest. NaN when
    fewer than k weeks are available (early history) -> those origins drop out.
    """
    s = weekly[["week_ending", col]].dropna().sort_values("week_ending")
    we = s["week_ending"].to_numpy()
    vals = s[col].to_numpy()
    out = {f"{col}_w{j}": np.full(len(months), np.nan) for j in range(k)}
    for i, m in enumerate(months):
        m_end = np.datetime64((m + pd.offsets.MonthEnd(0)).date())
        pos = int(np.searchsorted(we, m_end, side="right"))  # weeks ending <= M_end
        if pos >= k:
            window = vals[pos - k : pos][::-1]
            for j in range(k):
                out[f"{col}_w{j}"][i] = window[j]
    return pd.DataFrame(out, index=months)


def _add_lags(panel: pd.DataFrame, claims: pd.DataFrame, cols: list[str]) -> list[str]:
    """Attach weekly-lag features for each series in ``cols``; return their names."""
    names: list[str] = []
    for col in cols:
        lags = _weekly_lags(claims, col, panel.index, K)
        for name in lags.columns:
            panel[name] = lags[name]
            names.append(name)
    return names


def _rank(results: list[tuple[str, dict]], key: str, reverse: bool, fmt) -> None:
    for name, s in sorted(results, key=lambda kv: kv[1][key], reverse=reverse):
        print(f"  {name:<26} {fmt(s)}")


def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    claims = data.pull_claims_national(c)
    trends = data.pull_trends(c)

    # ===================== NFP headline ===================================== #
    nfp, ng = nfp_panel.build_panel(
        bls=data.pull_bls_series(nfp_panel.BLS_SERIES, c),
        claims=claims,
        adp=data.pull_adp_monthly(c),
        pulse=data.pull_adp_pulse(c),
        trends=trends,
        challenger=data.pull_challenger(c),
    )
    midas_cols = _add_lags(nfp, claims, ["claims_initial_sa", "claims_continued_sa"])
    mom = ng["momentum"]
    print(f"\nNFP headline (K={K} weekly lags x 2 series = {len(midas_cols)} MIDAS features)")
    print("=" * 104)
    nfp_specs = {
        "baseline rw": None,
        "baseline mean3": None,
        "monthly mom+claims": mom + ng["claims"],
        "MIDAS claims-only": midas_cols,
        "MIDAS mom+claims": mom + midas_cols,
    }
    base = nfp_h.walk_forward_baselines(nfp)
    results = []
    for name, cols in nfp_specs.items():
        if cols is None:
            col = "rw" if "rw" in name else "mean3"
            s = nfp_h.score(base["y_true"].values, base[col].values)
        else:
            preds = nfp_h.walk_forward_model(nfp, cols).dropna(subset=["pred", "y_true"])
            s = nfp_h.score(preds["y_true"].values, preds["pred"].values)
        results.append((name, s))
    _rank(results, "MAE", reverse=False, fmt=nfp_h._fmt)

    # ===================== Unemployment rate ================================ #
    ur, ug = ur_panel.build_panel(
        bls=data.pull_bls_series(ur_panel.BLS_SERIES, c), claims=claims, trends=trends
    )
    midas_cols = _add_lags(ur, claims, ["iur_sa", "claims_continued_sa"])
    mom = ug["momentum"]
    print(f"\nUnemployment rate (K={K} weekly lags x 2 series = {len(midas_cols)} MIDAS features)")
    print("=" * 104)
    ur_specs = {
        "baseline rw": ("y_chg", None),
        "monthly chg: mom+iur+claims": ("y_chg", mom + ug["iur"] + ug["claims"]),
        "MIDAS chg: iur+claims-only": ("y_chg", midas_cols),
        "MIDAS chg: mom+iur+claims": ("y_chg", mom + midas_cols),
        "MIDAS level: mom+iur+claims": ("y_level", mom + midas_cols),
    }
    base = ur_h.walk_forward_baselines(ur)
    results = []
    for name, (target, cols) in ur_specs.items():
        if cols is None:
            s = ur_h.score(base["true"].values, base["rw"].values, base["last"].values)
        else:
            preds = ur_h.walk_forward_model(ur, cols, target).dropna(subset=["pred"])
            s = ur_h.score(preds["true"].values, preds["pred"].values, preds["last"].values)
        results.append((name, s))
    _rank(results, "exact%", reverse=True, fmt=ur_h._fmt)


if __name__ == "__main__":
    run()
