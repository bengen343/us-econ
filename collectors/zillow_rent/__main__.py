from collectors.common import run_collector
from collectors.zillow_rent.collect import collect

if __name__ == "__main__":
    run_collector("zillow_rent", collect)
