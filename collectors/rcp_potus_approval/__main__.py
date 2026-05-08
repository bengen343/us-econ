from collectors.common import run_collector
from collectors.rcp_potus_approval.collect import collect

if __name__ == "__main__":
    run_collector("rcp_potus_approval", collect)
