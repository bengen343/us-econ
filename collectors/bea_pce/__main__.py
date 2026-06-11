from collectors.bea_pce.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("bea_pce", collect)
