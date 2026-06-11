from collectors.bea_vehicles.collect import collect
from collectors.common import run_collector

if __name__ == "__main__":
    run_collector("bea_vehicles", collect)
