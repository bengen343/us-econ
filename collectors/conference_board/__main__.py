from collectors.common import run_collector
from collectors.conference_board.collect import collect

if __name__ == "__main__":
    run_collector("conference_board", collect)
