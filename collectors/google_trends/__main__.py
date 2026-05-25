from collectors.common import run_collector
from collectors.google_trends.collect import collect

if __name__ == "__main__":
    run_collector("google_trends", collect)
