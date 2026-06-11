from collectors.census_home_sales.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("census_home_sales", collect)
