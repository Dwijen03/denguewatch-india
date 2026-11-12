"""Step 10: run the jobs on a timetable while the app is open (APScheduler, India time).

    daily_update     every day   06:30
    weekly_reports   Monday      07:00   (NCDC usually posts the new week's PDF early in the week)
    monthly_retrain  1st of month 07:30

A laptop is not always switched on at those times, so on start-up any job whose last
successful run is too old (see jobs.MAX_AGE) is run once ("catch-up"), one after another.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from denguewatch.scheduler.jobs import due_jobs, mark_interrupted, run_job

log = logging.getLogger(__name__)
TZ = "Asia/Kolkata"

SCHEDULE = {
    "daily_update": CronTrigger(hour=6, minute=30, timezone=TZ),
    "weekly_reports": CronTrigger(day_of_week="mon", hour=7, minute=0, timezone=TZ),
    "monthly_retrain": CronTrigger(day=1, hour=7, minute=30, timezone=TZ),
}

_scheduler: BackgroundScheduler | None = None


def catch_up() -> list[str]:
    """Run every overdue job once, in order (reports before scores, retrain last)."""
    try:
        mark_interrupted()
        jobs = due_jobs()
    except Exception:  # noqa: BLE001 - e.g. database not reachable yet
        log.exception("Could not check which jobs are due")
        return []
    order = ["weekly_reports", "daily_update", "monthly_retrain"]
    jobs = sorted(jobs, key=order.index)
    log.info("Catch-up: %s", ", ".join(jobs) if jobs else "nothing overdue")
    for name in jobs:
        run_job(name)
    return jobs


def start_scheduler(catch_up_delay: int = 30) -> BackgroundScheduler | None:
    """Start the background scheduler once per process. Returns None if switched off."""
    global _scheduler
    if os.getenv("ENABLE_SCHEDULER", "1") != "1":
        log.info("Scheduler switched off (ENABLE_SCHEDULER=0)")
        return None
    if _scheduler is not None:
        return _scheduler
    sched = BackgroundScheduler(timezone=TZ, job_defaults={
        "coalesce": True,            # several missed runs -> run once
        "max_instances": 1,          # never two copies of the same job
        "misfire_grace_time": 6 * 3600,
    })
    for name, trigger in SCHEDULE.items():
        sched.add_job(run_job, trigger, args=[name], id=name, name=name)
    if catch_up_delay >= 0:
        sched.add_job(catch_up, DateTrigger(run_date=datetime.now().astimezone() + timedelta(seconds=catch_up_delay)),
                      id="catch_up", name="catch_up")
    sched.start()
    _scheduler = sched
    for job in sched.get_jobs():
        log.info("Scheduled %-16s next run %s", job.name, job.next_run_time)
    return sched


def next_runs() -> list[dict]:
    """Next run time of each scheduled job (for the API/dashboard)."""
    if _scheduler is None:
        return []
    return [{"job": j.name, "next_run": j.next_run_time.isoformat(timespec="minutes")}
            for j in _scheduler.get_jobs() if j.next_run_time]


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
