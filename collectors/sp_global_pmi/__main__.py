from collectors.common import run_collector
from collectors.sp_global_pmi.collect import collect

if __name__ == "__main__":
    run_collector("sp_global_pmi", collect)
