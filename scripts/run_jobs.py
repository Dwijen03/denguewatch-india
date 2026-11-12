"""Step 10: run the automatic update jobs once, without starting the web app.

    python scripts/run_jobs.py              # run whatever is due (used by Windows Task Scheduler)
    python scripts/run_jobs.py --status     # show when each job last succeeded and whether it is due
    python scripts/run_jobs.py --job daily_update      # force one job
    python scripts/run_jobs.py --job weekly_reports
    python scripts/run_jobs.py --job monthly_retrain
    python scripts/run_jobs.py --job news_signals      # AI news agent (Step 11)
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from denguewatch.scheduler.jobs import JOBS, MAX_AGE, due_jobs, last_success, mark_interrupted, run_job  # noqa: E402

ORDER = ["weekly_reports", "daily_update", "monthly_retrain", "news_signals"]


def show_status() -> None:
    last, now = last_success(), datetime.now(UTC)
    print(f"{'job':<17}{'last success':<22}due?")
    for job, age in MAX_AGE.items():
        ts = last[job]
        due = ts is None or now - ts > age
        print(f"{job:<17}{ts.astimezone().strftime('%Y-%m-%d %H:%M') if ts else 'never':<22}{'YES' if due else 'no'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--job", choices=list(JOBS), help="run this job now, even if it is not due")
    parser.add_argument("--status", action="store_true", help="only show the status of each job")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    mark_interrupted()
    if args.status:
        show_status()
        return 0
    jobs = [args.job] if args.job else sorted(due_jobs(), key=ORDER.index)
    print("Jobs to run:", ", ".join(jobs) if jobs else "none - everything is up to date")
    failed = [name for name in jobs if run_job(name) is None]
    print()
    show_status()
    if failed:
        print("\nFAILED:", ", ".join(failed), "- see the messages above and /api/v1/pipeline-runs")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
