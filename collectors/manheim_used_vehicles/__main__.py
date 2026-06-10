from collectors.common import run_collector
from collectors.manheim_used_vehicles.collect import collect

if __name__ == "__main__":
    run_collector("manheim_used_vehicles", collect)
