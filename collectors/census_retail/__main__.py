from collectors.census_retail.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("census_retail", collect)
