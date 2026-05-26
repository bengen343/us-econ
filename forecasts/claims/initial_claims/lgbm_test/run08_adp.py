"""Iteration 8: LGBM + ADP weekly NER, lag sweep.

ADP weekly NER history publication lag is ~53 days (~8 weeks), so the
freshest ADP value available at origin T is from ~T-8 weeks. We sweep
backward lags 8, 9, 10, 12, 16, 20.

The level (ner_sa is ~132M and barely moves week-to-week, ±0.07%) is
likely too smooth to be useful directly; the week-over-week DIFF is the
informative signal. We test both, plus a few subaggregations.

Run:
    .\.venv\Scripts\python.exe forecasts\claims\initial_claims\lgbm_test\run08_adp.py
"""

from __future__ import annotations

import pandas as pd

from harness import EvalSpec, FeatureSpec, build_panel, fmt_summary, load_data, walk_forward_eval


def main():
    data = load_data()
    adp_cols = [c for c in data["adp"].columns if c != "week_ending"]
    ner_sa_us = "adp_ner_sa_us"
    ner_us = "adp_ner_us"

    print(f"ADP signals available: {len(adp_cols)} columns")
    print(f"  Headline: {ner_sa_us} (SA total US employment, ~132M)")
    print()

    eval_spec = EvalSpec(train_start="2022-01-01", eval_start="2024-07-01")
    floor = FeatureSpec(target_lags=list(range(1, 9)), seasonal=True)

    variants = [
        ("floor (target only)", floor),

        # Level-only single-lag sweep on US headline SA
        ("ner_sa_us level lag 8",    FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_lags": [8]})),
        ("ner_sa_us level lag 9",    FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_lags": [9]})),
        ("ner_sa_us level lag 10",   FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_lags": [10]})),
        ("ner_sa_us level lag 12",   FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_lags": [12]})),
        ("ner_sa_us level lag 16",   FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_lags": [16]})),

        # Diff-only single-lag sweep on US headline SA (WoW change in SA employment level)
        ("ner_sa_us diff lag 8",     FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [8]})),
        ("ner_sa_us diff lag 9",     FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [9]})),
        ("ner_sa_us diff lag 10",    FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [10]})),
        ("ner_sa_us diff lag 12",    FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [12]})),

        # NSA variant: ner (raw level) lag 8 - may capture seasonal info SA strips
        ("ner_us level lag 8",       FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_us], "adp_lags": [8]})),
        ("ner_us diff lag 8",        FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_us], "adp_diff_lags": [8]})),

        # Diff-window: a few backward diffs to capture momentum
        ("ner_sa_us diff lags 8,9,10", FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [8, 9, 10]})),
        ("ner_sa_us diff lags 8..12", FeatureSpec(**{**floor.__dict__, "adp_cols": [ner_sa_us], "adp_diff_lags": [8, 9, 10, 11, 12]})),

        # Add some industry breakouts (manufacturing + construction = layoff-heavy)
        ("ind manufacturing diff lag 8", FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_sa_ind_manufacturing"], "adp_diff_lags": [8]})),
        ("ind construction diff lag 8",  FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_sa_ind_construction"], "adp_diff_lags": [8]})),
        ("ind professional_and_business_services diff lag 8",
            FeatureSpec(**{**floor.__dict__, "adp_cols": ["adp_ner_sa_ind_professional_and_business_services"], "adp_diff_lags": [8]})),

        # Combine US headline + a few industry diffs at lag 8
        ("US + mfg + constr diff lag 8", FeatureSpec(**{**floor.__dict__,
            "adp_cols": [ner_sa_us, "adp_ner_sa_ind_manufacturing", "adp_ner_sa_ind_construction"],
            "adp_diff_lags": [8]})),

        # All establishment sizes diff lag 8 (5 features)
        ("all sizes diff lag 8", FeatureSpec(**{**floor.__dict__,
            "adp_cols": [c for c in adp_cols if c.startswith("adp_ner_sa_size_")],
            "adp_diff_lags": [8]})),
    ]

    print("=" * 104)
    print("Iteration 8: LGBM + ADP weekly NER, lag sweep")
    print(f"Train: {eval_spec.train_start}+   Eval: {eval_spec.eval_start}+  (ADP pub_lag ~53d => k>=8 valid)")
    print("=" * 104)

    rows = []
    for label, spec in variants:
        panel, feats = build_panel(data, spec)
        r = walk_forward_eval(panel, feats, eval_spec, model="lgbm", refit_every=4)
        rows.append({
            "variant": label,
            "n_features": len(feats),
            "n_obs": r["n"],
            "mae": r["model_mae"],
            "rmse": r["model_rmse"],
            "bias": r["model_bias"],
        })
        print(fmt_summary(r, label))

    df = pd.DataFrame(rows).sort_values("mae")
    df.to_csv("forecasts/claims/initial_claims/lgbm_test/runs/iter8_results.csv", index=False)
    print()
    print("Leaderboard (best first):")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
