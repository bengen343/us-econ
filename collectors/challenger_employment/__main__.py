from collectors.challenger_employment.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("challenger_employment", collect)
