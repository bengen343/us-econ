r"""Expanded multivariate retry: does adding the new timely survey data — ISM
Manufacturing/Services (employment + headline PMI) and the Conference Board
labor differential & jobs expectations — improve the NFP and unemployment-rate
nowcasts over our prior best?

The deep-research review flagged these as the most promising *new* inputs: all
are released ahead of the Employment Situation (ISM Manufacturing 1st business
day, Services 3rd, Conference Board ~last Tuesday of the month), so month M is a
contemporaneous pre-release nowcast signal, and all have deep history (CB to
1967, ISM Mfg to 1949, ISM Svc to 1997) — unlike Google Trends they do NOT
shrink the 2011+ test window.

Reuses each target's panel + walk_forward_model + scoring, so the expanded specs
are scored on the SAME COVID-masked origins as the prior monthly best.

Timing caveat: ISM Services (3rd business day) can fall just after the jobs
report in months that start on a Friday; we use contemporaneous M anyway (the
standard nowcasting choice). If a winner leans on ism_svc_*, re-check with a
1-month lag before trusting it live.

Run:  .\.venv\Scripts\python.exe -m forecasts.bls_employment.multivariate
"""

from __future__ import annotations

import pandas as pd

from forecasts.bls_employment import data
from forecasts.bls_employment.payrolls_headline import harness as nfp_h
from forecasts.bls_employment.payrolls_headline import panel as nfp_panel
from forecasts.bls_employment.unemployment_rate import harness as ur_h
from forecasts.bls_employment.unemployment_rate import panel as ur_panel


def _add_surveys(panel: pd.DataFrame, ism: pd.DataFrame, cb: pd.DataFrame) -> dict[str, list[str]]:
    """Attach contemporaneous-M ISM + CB features; return {group: cols}."""
    i = ism.set_index("month")
    c = cb.set_index("month")
    for col in i.columns:
        panel[col] = i[col].reindex(panel.index)
    # CB derived signals: labor differential level + momentum, net jobs outlook.
    panel["cb_labor_diff"] = c["cb_labor_differential"].reindex(panel.index)
    panel["cb_labor_diff_mom"] = panel["cb_labor_diff"].diff()
    panel["cb_exp_jobs_net"] = (
        c["cb_exp_jobs_more"] - c["cb_exp_jobs_fewer"]
    ).reindex(panel.index)
    return {
        "ism": ["ism_mfg_employment", "ism_svc_employment", "ism_mfg_pmi", "ism_svc_pmi"],
        "cb": ["cb_labor_diff", "cb_labor_diff_mom", "cb_exp_jobs_net"],
    }


def _run(panel, walk, score, fmt, specs, *, target=None, baseline=None):
    results = []
    if baseline is not None:
        results.append(baseline)
    for name, cols in specs.items():
        preds = (walk(panel, cols, target) if target else walk(panel, cols)).dropna(
            subset=["pred"]
        )
        if target:  # UR: score(true, pred, last)
            s = score(preds["true"].values, preds["pred"].values, preds["last"].values)
        else:  # NFP: score(y_true, pred)
            s = score(preds["y_true"].values, preds["pred"].values)
        results.append((name, s))
    return results


def run() -> None:
    print("Pulling BigQuery inputs (read-only)...")
    c = data._client()
    ism = data.pull_ism(c)
    cb = data.pull_conference_board(c)
    claims = data.pull_claims_national(c)
    trends = data.pull_trends(c)

    # ===================== NFP headline ===================================== #
    nfp, ng = nfp_panel.build_panel(
        bls=data.pull_bls_series(nfp_panel.BLS_SERIES, c), claims=claims,
        adp=data.pull_adp_monthly(c), pulse=data.pull_adp_pulse(c),
        trends=trends, challenger=data.pull_challenger(c),
    )
    sg = _add_surveys(nfp, ism, cb)
    mc = ng["momentum"] + ng["claims"]
    print(f"\nNFP headline  (ISM {sg['ism']}, CB {sg['cb']})")
    print("=" * 104)
    specs = {
        "mom+claims (prior best)": mc,
        "mom+claims+ism": mc + sg["ism"],
        "mom+claims+cb": mc + sg["cb"],
        "mom+claims+ism+cb": mc + sg["ism"] + sg["cb"],
        "surveys only (ism+cb)": sg["ism"] + sg["cb"],
        "mom+ism+cb (no claims)": ng["momentum"] + sg["ism"] + sg["cb"],
    }
    results = _run(nfp, nfp_h.walk_forward_model, nfp_h.score, nfp_h._fmt, specs)
    for name, s in sorted(results, key=lambda kv: kv[1]["MAE"]):
        print(f"  {name:<26} {nfp_h._fmt(s)}")

    # ===================== Unemployment rate ================================ #
    ur, ug = ur_panel.build_panel(
        bls=data.pull_bls_series(ur_panel.BLS_SERIES, c), claims=claims, trends=trends
    )
    _add_surveys(ur, ism, cb)
    base = ug["momentum"] + ug["iur"] + ug["claims"]
    print("\nUnemployment rate  (change framing)")
    print("=" * 104)
    specs = {
        "mom+iur+claims (prior best)": base,
        "+ism": base + sg["ism"],
        "+cb": base + sg["cb"],
        "+ism+cb": base + sg["ism"] + sg["cb"],
        "surveys only (ism+cb)": sg["ism"] + sg["cb"],
    }
    results = _run(ur, ur_h.walk_forward_model, ur_h.score, ur_h._fmt, specs, target="y_chg")
    for name, s in sorted(results, key=lambda kv: (-kv[1]["exact%"], kv[1]["MAE"])):
        print(f"  {name:<28} {ur_h._fmt(s)}")


if __name__ == "__main__":
    run()
