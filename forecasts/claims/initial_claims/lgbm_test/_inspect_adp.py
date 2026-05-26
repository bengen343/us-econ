"""Probe ADP value scales / units."""
from google.cloud import bigquery

c = bigquery.Client(project="us-econ-51920")

print("=== ner_history: National-US weekly, recent values ===")
q = """
SELECT observation_date, ner, ner_sa
FROM `us-econ-51920.adp_employment.ner_history`
WHERE timestep = 'W'
  AND aggregation = 'National'
  AND category = 'U.S.'
ORDER BY observation_date DESC
LIMIT 20
"""
for row in c.query(q).result():
    print(f"  {row.observation_date}  ner={row.ner!s:>14}  ner_sa={row.ner_sa!s:>14}")

print()
print("=== ner_history: weekly aggregation values vs claims, recent ===")
# What does the national U.S. ner_sa look like compared to claims SA?
q2 = """
WITH adp AS (
  SELECT observation_date AS week_ending, ner_sa AS adp_ner_sa
  FROM `us-econ-51920.adp_employment.ner_history`
  WHERE timestep = 'W' AND aggregation = 'National' AND category = 'U.S.'
)
SELECT a.week_ending, a.adp_ner_sa, c.value AS claims_sa
FROM adp a
LEFT JOIN `us-econ-51920.claims.fct_sa_input` c USING (week_ending)
ORDER BY a.week_ending DESC
LIMIT 15
"""
for row in c.query(q2).result():
    print(f"  {row.week_ending}  adp_ner_sa={row.adp_ner_sa!s:>14}  claims_sa={row.claims_sa!s:>10}")

print()
print("=== weekly ADP categories joinable to claims grid: how many week_endings overlap with our 2022+ training? ===")
q3 = """
WITH adp AS (
  SELECT observation_date AS week_ending
  FROM `us-econ-51920.adp_employment.ner_history`
  WHERE timestep = 'W' AND aggregation = 'National' AND category = 'U.S.'
)
SELECT
  COUNT(*) AS adp_weeks_total,
  SUM(IF(week_ending >= '2022-01-01', 1, 0)) AS adp_weeks_2022_plus,
  SUM(IF(week_ending >= '2024-07-01', 1, 0)) AS adp_weeks_2024_07_plus,
  MIN(week_ending) AS first_wk, MAX(week_ending) AS last_wk
FROM adp
"""
for row in c.query(q3).result():
    print(f"  ADP weekly U.S. coverage: total={row.adp_weeks_total}, 2022+={row.adp_weeks_2022_plus}, 2024-07+={row.adp_weeks_2024_07_plus}  ({row.first_wk}..{row.last_wk})")

print()
print("=== weekday of week_ending for ADP weekly — does it match Saturday like claims/trends? ===")
q4 = """
SELECT FORMAT_DATE('%A', observation_date) AS wd, COUNT(*) n
FROM `us-econ-51920.adp_employment.ner_history`
WHERE timestep = 'W' AND aggregation = 'National' AND category = 'U.S.'
GROUP BY wd
"""
for row in c.query(q4).result():
    print(f"  {row.wd}: {row.n}")
