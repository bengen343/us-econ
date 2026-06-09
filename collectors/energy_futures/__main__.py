from collectors.common import run_collector
from collectors.energy_futures.collect import collect

if __name__ == "__main__":
    run_collector("energy_futures", collect)
