from collectors.common import run_collector
from collectors.nahb_hmi.collect import collect

if __name__ == "__main__":
    run_collector("nahb_hmi", collect)
