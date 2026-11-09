"""Headless runner (no UI).

    python run.py              # one full cycle, all businesses
    python run.py trading      # one cycle, one business
    python run.py --loop 60    # run forever, a cycle every 60 minutes
"""
import json
import logging
import sys
import time

from dotenv import load_dotenv

load_dotenv()

from agency.orchestrator import run_cycle  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--loop"]:
        minutes = float(args[1]) if len(args) > 1 else 60
        while True:
            run_cycle()
            time.sleep(minutes * 60)
    else:
        print(json.dumps(run_cycle(only=args or None), indent=2, default=str))
