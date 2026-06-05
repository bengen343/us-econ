from collectors.common import run_collector
from collectors.eia_petroleum.collect import collect

if __name__ == "__main__":
    run_collector("eia_petroleum", collect)
