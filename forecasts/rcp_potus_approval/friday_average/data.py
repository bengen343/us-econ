"""Read-only BigQuery data layer for the RCP Friday-average forecast.

Everything the model needs is derived from the single append-only snapshot table
``rcp_potus_approval.polls`` (written daily by the off-platform collector and
backfilled with the 2nd-term Wayback history). The forecast itself touches no
external site, so it runs on Cloud Run even though the collector cannot.

The published RCP average is the *simple unweighted mean of the polls listed on
the page* (verified to 0.1 on every overlapping day), so the target series is the
``rcp_average`` row when present and the mean of the day's polls otherwise.

Derived structures (all keyed by ``observation_date`` = the as-of date a snapshot
was captured; for live rows this is the collection date, for backfilled rows the
Wayback capture date):

  * windows[D]   -> [(pollster, survey_end, approve)]   the poll set on the page at D
  * truth[D]     -> float                                the published average at D
  * releases[p]  -> sorted [(first_seen, survey_end, approve)]  per-pollster history,
                    first_seen = first observation_date the poll appears (the live
                    collector makes this the release date; backfilled history is
                    coarser but only the impactful weekly/monthly pollsters matter
                    and their per-cycle cadence survives the coarsening).
"""

from __future__ import annotations

import statistics
from datetime import date

from google.cloud import bigquery

PROJECT = "us-econ-51920"
SOURCE_TABLE = "rcp_potus_approval.polls"
TERM_START = date(2025, 1, 27)
AVERAGE_POLLSTER = "rcp_average"


def pull_rows(client: bigquery.Client) -> list[dict]:
    """Distinct snapshot rows (latest ingest vintage per key), 2nd term onward."""
    sql = f"""
    WITH dedup AS (
      SELECT observation_date, pollster, survey_start, survey_end,
             approve_pct, disapprove_pct,
             ROW_NUMBER() OVER (
               PARTITION BY observation_date, pollster, survey_start, survey_end
               ORDER BY ingested_at DESC) AS rn
      FROM `{PROJECT}.{SOURCE_TABLE}`
      WHERE observation_date >= DATE('{TERM_START.isoformat()}')
    )
    SELECT observation_date, pollster, survey_start, survey_end,
           approve_pct, disapprove_pct
    FROM dedup
    WHERE rn = 1
    """
    rows = []
    for r in client.query(sql).result():
        rows.append(
            {
                "observation_date": r.observation_date,
                "pollster": r.pollster,
                "survey_end": r.survey_end,
                "approve": float(r.approve_pct) if r.approve_pct is not None else None,
            }
        )
    return rows


def build_windows(rows: list[dict]) -> dict[date, list[tuple[str, date, float]]]:
    win: dict[date, list[tuple[str, date, float]]] = {}
    for r in rows:
        if r["pollster"] == AVERAGE_POLLSTER or r["survey_end"] is None or r["approve"] is None:
            continue
        win.setdefault(r["observation_date"], []).append(
            (r["pollster"], r["survey_end"], r["approve"])
        )
    return {d: polls for d, polls in win.items() if len(polls) >= 3}


def build_truth(rows: list[dict]) -> dict[date, float]:
    """Published average per observation_date: the rcp_average row if present,
    else the mean of that day's polls (matches RCP's simple-mean methodology)."""
    avg_row: dict[date, float] = {}
    poll_vals: dict[date, list[float]] = {}
    for r in rows:
        if r["pollster"] == AVERAGE_POLLSTER:
            if r["approve"] is not None:
                avg_row[r["observation_date"]] = r["approve"]
        elif r["approve"] is not None and r["survey_end"] is not None:
            poll_vals.setdefault(r["observation_date"], []).append(r["approve"])
    truth: dict[date, float] = dict(avg_row)  # the published daily-average history
    for d, vals in poll_vals.items():         # fill the few days that lack an average row
        if d not in truth:
            truth[d] = round(statistics.mean(vals), 1)
    return truth


def build_releases(
    rows: list[dict], truth: dict[date, float]
) -> dict[str, list[tuple[date, date, float]]]:
    """Per-pollster release history. One entry per distinct (pollster, survey_end);
    first_seen = earliest observation_date it appears. House offset is computed by
    the model from (approve - truth[first_seen])."""
    seen: dict[tuple[str, date], dict] = {}
    for r in rows:
        if r["pollster"] == AVERAGE_POLLSTER or r["survey_end"] is None or r["approve"] is None:
            continue
        key = (r["pollster"], r["survey_end"])
        cur = seen.get(key)
        if cur is None or r["observation_date"] < cur["first_seen"]:
            seen[key] = {
                "first_seen": r["observation_date"],
                "survey_end": r["survey_end"],
                "approve": r["approve"],
            }
    out: dict[str, list[tuple[date, date, float]]] = {}
    for (pollster, _end), rec in seen.items():
        out.setdefault(pollster, []).append(
            (rec["first_seen"], rec["survey_end"], rec["approve"])
        )
    for v in out.values():
        v.sort()
    return out


def load_all(client: bigquery.Client):
    rows = pull_rows(client)
    truth = build_truth(rows)
    return build_windows(rows), truth, build_releases(rows, truth)
