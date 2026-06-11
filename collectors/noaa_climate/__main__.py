from collectors.common import run_collector
from collectors.noaa_climate.collect import collect

if __name__ == "__main__":
    run_collector("noaa_climate", collect)
