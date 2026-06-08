from collectors.bls_cpi_weights.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("bls_cpi_weights", collect)
