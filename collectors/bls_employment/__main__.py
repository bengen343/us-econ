from collectors.bls_employment.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("bls_employment", collect)
