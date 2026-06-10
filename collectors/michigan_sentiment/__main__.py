from collectors.common import run_collector
from collectors.michigan_sentiment.collect import collect

if __name__ == "__main__":
    run_collector("michigan_sentiment", collect)
