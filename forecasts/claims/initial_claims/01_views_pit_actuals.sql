-- Forecasting harness for SA initial claims — point-in-time + actuals layer.
--
-- Project/dataset are hardcoded to us-econ-51920.claims (matches
-- terraform/terraform.tfvars). Change both tokens if you target another env.
--
-- Run order: 01 (this) -> 02 (procedure) -> 03 (scoring). 99 has examples.

-- ---------------------------------------------------------------------------
-- fc_pit_series: the series as it was knowable at a given forecast origin.
--
-- For origin O and an upper week bound MW, returns one (week_ending, value)
-- per week_ending <= MW, choosing the vintage we would have had on O.
--
--   p_use_pit = TRUE  : among vintages with vintage_date <= O, take the latest.
--                        If a week has NO vintage <= O (deep history that was
--                        backfilled in a single later DOLETA run), fall back
--                        to the EARLIEST vintage we ever captured — the best
--                        proxy for "as known then" when no real history exists.
--   p_use_pit = FALSE : ignore vintages, take the fully-revised (latest) value.
--
-- p_max_week is separate from p_origin so the same function serves training
-- data (MW = origin) and known-ahead inputs like the seasonal factor, whose
-- week_ending is in the future relative to the origin (MW = origin + horizon).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE FUNCTION `us-econ-51920.claims.fc_pit_series`(
  p_series   STRING,
  p_origin   DATE,
  p_max_week DATE,
  p_use_pit  BOOL
) AS (
  WITH ranked AS (
    SELECT
      week_ending,
      value,
      ROW_NUMBER() OVER (
        PARTITION BY week_ending
        ORDER BY
          ((NOT p_use_pit) OR (vintage_date <= p_origin)) DESC,
          IF((NOT p_use_pit) OR (vintage_date <= p_origin), vintage_date, NULL) DESC,
          vintage_date ASC,
          ingested_at DESC
      ) AS rn
    FROM `us-econ-51920.claims.weekly_claims`
    WHERE series_id = p_series
      AND week_ending <= p_max_week
      AND value IS NOT NULL
  )
  SELECT week_ending, value
  FROM ranked
  WHERE rn = 1
);

-- ---------------------------------------------------------------------------
-- fc_actuals_as_reported: the first-published SA initial-claims value per week
-- (the "as reported" headline). Earliest vintage across the DOLETA XML and the
-- DOL press-release PDF, since the press advance number can precede the XML.
-- Caveat: for weeks predating the collector going live we only ever captured a
-- single, already-revised vintage, so "as reported" == revised there.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `us-econ-51920.claims.fc_actuals_as_reported` AS
WITH sa AS (
  SELECT week_ending, value, vintage_date, ingested_at
  FROM `us-econ-51920.claims.weekly_claims`
  WHERE series_id IN ('doleta.us.initial_claims.sa', 'press.us.initial_claims.sa')
    AND value IS NOT NULL
),
ranked AS (
  SELECT
    week_ending,
    value,
    ROW_NUMBER() OVER (
      PARTITION BY week_ending ORDER BY vintage_date ASC, ingested_at ASC
    ) AS rn
  FROM sa
)
SELECT week_ending, value AS sa_as_reported
FROM ranked
WHERE rn = 1;

-- ---------------------------------------------------------------------------
-- fc_actuals_latest: the fully-revised SA value per week (DOLETA XML only;
-- the press PDF is never revised). Useful as an alternative scoring target.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW `us-econ-51920.claims.fc_actuals_latest` AS
WITH ranked AS (
  SELECT
    week_ending,
    value,
    ROW_NUMBER() OVER (
      PARTITION BY week_ending ORDER BY vintage_date DESC, ingested_at DESC
    ) AS rn
  FROM `us-econ-51920.claims.weekly_claims`
  WHERE series_id = 'doleta.us.initial_claims.sa'
    AND value IS NOT NULL
)
SELECT week_ending, value AS sa_latest
FROM ranked
WHERE rn = 1;
