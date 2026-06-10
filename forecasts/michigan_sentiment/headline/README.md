# Michigan consumer sentiment forecasts (preliminary + final)

**Targets:** the University of Michigan Index of Consumer Sentiment (ICS),
both monthly prints — the **preliminary** (~2nd Friday of the survey month)
and the **final** (~4th Friday), released Fridays 10:00 ET. The pending
release alternates, so the job `michigan-sentiment-forecast` (daily 07:30 MT)
emits exactly one target per run into `michigan_sentiment.forecast_headline`
(+`_current` view): level + change, where change is vs the prior final for
the prelim and vs the published prelim (the revision) for the final.

## The structure of the problem

- prelim↔final correlation is ~0.97 — the final's sample *contains* the
  prelim interviews. So the final target is really the **revision**
  (final − prelim, σ≈1.4 pts), and the bar is the zero-revision baseline.
- The prelim change (prelim_M − final_{M-1}, σ≈3.9 pts) is the hard target —
  consensus misses by 2-3 points in volatile regimes.
- **Survey-window alignment is the edge**: prelim interviews run ~the 25th of
  M-1 through ~the 7th of M, and the final adds interviews through ~the 21st.
  Features measured over those exact windows beat calendar-month versions of
  the same inputs across the board (`survey_window_mean` in data.py).

## Winning models

Walk-forward expanding-window OLS in index points, MIN_TRAIN=96 (prelim
history starts 1997-06):

- **Prelim (`gas_sp_sw_v1`)**: Δ% gasoline Gulf Coast spot + Δ% S&P 500, each
  survey-window-to-survey-window.
- **Revision (`gas_sp_post_v1`)**: the same pair, post-prelim window (days
  8-21 / 8-18 of M) vs the prelim window.

Backtest (2010+, COVID 2020-03..2021-06 masked; Michigan history is never
revised so latest-vintage backtesting is exact):

| target | spec | MAE | RMSE | baseline RMSE | dir% |
|---|---|---|---|---|---|
| prelim | gas_sp_sw | 2.84 | **3.61** | 3.89 (carry-forward) | 59 |
| prelim 2017+ | gas_sp_sw | 2.76 | **3.58** | 3.92 | 61 |
| revision | gas_sp_post | 1.02 | **1.35** | 1.43 (zero revision) | 65 |
| revision 2017+ | gas_sp_post | 0.91 | **1.22** | 1.22 | 64 |

Losers: SF Fed Daily News Sentiment Index (helped pre-2017 only — best spec
on 2010+ by 0.04 RMSE, worse on 2017+, and it's an external weekly-xlsx
dependency), EIA retail gasoline (consumers see retail, but the daily spot's
cadence beat the weekly series), Conference Board lag 1, own-history/momentum
(prelim-carry and final-momentum are *worse* than carry-forward), seasonal
terms, dense kitchen-sink specs, LightGBM.

First live call (made 2026-06-10): **June 2026 prelim 46.6 (+1.8 vs May's
44.8)**, releases 2026-06-12.

## Data flow

Production reads BigQuery only: `michigan_sentiment.surveys_of_consumers`
(the collector scrapes the SCA homepage Fridays; backfilled 1952+ finals /
1997+ prelims), `eia_petroleum.prices` (gas spot), `market_indexes.daily`
(S&P 500 — collector built for this forecast). The harness additionally pulls
Yahoo/SF-Fed/CB directly for the losing candidates.

## Caveats / revisit

- **The edge is honest but modest** (~8% RMSE vs carry-forward on the prelim;
  ~6% vs zero on the revision, mostly pre-2017). Sentiment prelims are
  genuinely hard; treat the point forecast with its ±2.8 MAE.
- **2017+ revision bias +0.3-0.4**: all specs (and the prelim-move
  continuation) over-predict revisions in the recent regime — revisions
  skewed negative 2022-26. An intercept-only recentering or asymmetric
  handling is the first thing to try with more data.
- **Partisan/idiosyncratic shifts** (e.g. the 2024-26 partisan-gap swings,
  survey methodology change to web in 2024-25) move the index in ways no
  market input captures — irreducible at this spec.
- The SF Fed DNSI is the strongest untested-in-production candidate: revisit
  if its weekly publication becomes reliable enough to collect, especially
  if the gasoline regime fades (it carried the pre-2017 window).
- Inflation expectations (year-ahead/long-run) are released with the same
  reports but not yet collected — both a candidate regressor and a candidate
  forecast target (extension: parse them from the data-site tables).
