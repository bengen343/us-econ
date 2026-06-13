from dataclasses import dataclass


@dataclass(frozen=True)
class TrendsTerm:
    """One Google Trends series we pull each run.

    kind:
      - 'query'    -- term is a literal search string ("file for unemployment").
      - 'topic'    -- term is a Knowledge Graph machine ID ("/m/07s_c"); Trends
                      treats it as a concept across synonyms/translations and
                      is stabler than a free-text query across renormalizations.
      - 'category' -- term is a Google Trends category numeric ID as STRING
                      (e.g. "60" = Jobs); aggregates many sub-terms.
    """
    series_id: str
    kind: str
    term: str
    description: str


GEO = "US"
# `today 5-y` is the longest pytrends timeframe that still yields WEEKLY
# granularity. Longer windows downgrade to monthly.
LOOKBACK_TIMEFRAME = "today 5-y"


TERMS: list[TrendsTerm] = [
    # --- Claims / unemployment (Choi & Varian 2009 signal + filing-intent variants) ---
    TrendsTerm(
        "trends.us.jobs_cat",
        "category", "60",
        "Trends category 'Jobs' (cat=60) -- Choi & Varian's original signal",
    ),
    TrendsTerm(
        "trends.us.unemployment_topic",
        "topic", "/m/07s_c",
        "Knowledge Graph topic 'Unemployment' (synonym/translation-stable)",
    ),
    TrendsTerm(
        "trends.us.q_file_for_unemployment",
        "query", "file for unemployment",
        "filing-intent literal query",
    ),
    TrendsTerm(
        "trends.us.q_unemployment_benefits",
        "query", "unemployment benefits",
        "benefits-focused filing-intent query",
    ),
    TrendsTerm(
        "trends.us.q_unemployment_office",
        "query", "unemployment office",
        "physical-filing-intent query",
    ),

    # --- AAA Gasoline (gas-price salience + discretionary-driving demand) ---
    TrendsTerm(
        "trends.us.gasoline_topic",
        "topic", "/m/05wy2",
        "Knowledge Graph topic 'Gasoline' (Fuel) -- price salience",
    ),
    TrendsTerm(
        "trends.us.q_gas_prices",
        "query", "gas prices",
        "concurrent gas-price salience query",
    ),
    TrendsTerm(
        "trends.us.q_road_trip",
        "query", "road trip",
        "discretionary-driving demand query",
    ),

    # --- Employment hiring side (complements the claims unemployment signals) ---
    TrendsTerm(
        "trends.us.q_jobs_near_me",
        "query", "jobs near me",
        "worker-side job-search demand",
    ),
    TrendsTerm(
        "trends.us.q_jobs_hiring",
        "query", "jobs hiring",
        "worker-side job-search demand (variant)",
    ),

    # --- Challenger layoff announcements ---
    TrendsTerm(
        "trends.us.q_layoffs",
        "query", "layoffs",
        "layoff-news awareness query",
    ),
]
