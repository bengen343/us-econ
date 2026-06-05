from collectors.bls_ppi.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("bls_ppi", collect)
