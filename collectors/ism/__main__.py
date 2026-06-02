from collectors.common import run_collector
from collectors.ism.collect import collect

if __name__ == "__main__":
    run_collector("ism", collect)
