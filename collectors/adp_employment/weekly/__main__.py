from collectors.adp_employment.weekly.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("adp_employment/weekly", collect)
