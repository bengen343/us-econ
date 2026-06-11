from collectors.common import run_collector
from collectors.fed_surveys.collect import collect

if __name__ == "__main__":
    run_collector("fed_surveys", collect)
