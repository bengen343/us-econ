from collectors.common import run_collector
from collectors.market_indexes.collect import collect

if __name__ == "__main__":
    run_collector("market_indexes", collect)
