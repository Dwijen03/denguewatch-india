"""Tests for Step 10 automation: incremental report ingestion, job locking, due-job logic,
retraining safeguard and the scheduler itself. None of these need the internet."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from denguewatch.ingestion.reports import ReportRef
from denguewatch.scheduler import jobs, runner

# ---------------------------------------------------------------- incremental ingestion

@pytest.fixture
def processed(tmp_path):
    """A tiny processed/ folder that already contains week 1 of 2026."""
    pd.DataFrame([{"year": 2026, "week": 1, "url": "u1", "file": "2026_week01.pdf",
                   "download_status": "ok", "dengue_rows": 1}]).to_csv(tmp_path / "reports_index.csv", index=False)
    pd.DataFrame([{"year": 2026, "week": 1, "file": "2026_week01.pdf", "state": "Kerala",
                   "district_raw": "Kollam", "cases": 12}]).to_csv(tmp_path / "dengue_rows_raw.csv", index=False)
    return tmp_path


@pytest.fixture
def fake_pipeline(monkeypatch):
    """Replace download / parse / clean / database steps with recorders."""
    calls = {"downloaded": [], "loaded": None}
    import denguewatch.ingestion.cleaning as cleaning
    import denguewatch.ingestion.districts as districts
    import denguewatch.ingestion.pipeline as pipeline

    def download_all(refs):
        calls["downloaded"] = [r.filename for r in refs]
        return pd.DataFrame([{"year": r.year, "week": r.week, "url": r.url, "file": r.filename,
                              "download_status": "ok"} for r in refs])

    def parse_all(index, workers=None):
        raw = pd.DataFrame([{"year": r.year, "week": r.week, "file": r.file, "state": "Kerala",
                             "district_raw": "Kollam", "cases": 5} for r in index.itertuples()])
        return raw, index.assign(dengue_rows=1)

    monkeypatch.setattr(pipeline, "download_all", download_all)
    monkeypatch.setattr(pipeline, "parse_all", parse_all)
    monkeypatch.setattr(districts, "DistrictMatcher", lambda: None)
    monkeypatch.setattr(cleaning, "clean_rows", lambda raw, matcher: raw.copy())
    monkeypatch.setattr(cleaning, "deduplicate", lambda clean: clean.drop_duplicates(["year", "week"]))
    monkeypatch.setattr(jobs, "load_reports", lambda ob, idx, eng: None)
    monkeypatch.setattr(jobs, "load_outbreaks", lambda ob, eng: calls.__setitem__("loaded", len(ob)))
    return calls


def refs(*weeks):
    return [ReportRef(2026, w, f"https://example.org/2026/week{w}.pdf") for w in weeks]


def test_only_new_reports_are_downloaded(processed, fake_pipeline):
    res = jobs.ingest_new_reports(refs=refs(1, 2, 3), processed_dir=processed, engine=object())
    assert fake_pipeline["downloaded"] == ["2026_week02.pdf", "2026_week03.pdf"]
    assert res["new_reports"] == 2 and res["new_rows"] == 2 and res["latest"] == "2026-W03"
    assert len(pd.read_csv(processed / "reports_index.csv")) == 3
    assert len(pd.read_csv(processed / "dengue_rows_raw.csv")) == 3       # old row kept, 2 added
    assert fake_pipeline["loaded"] == 3                                    # all outbreaks upserted


def test_nothing_new_changes_nothing(processed, fake_pipeline):
    before = (processed / "dengue_rows_raw.csv").read_text()
    res = jobs.ingest_new_reports(refs=refs(1), processed_dir=processed, engine=object())
    assert res["new_reports"] == 0 and fake_pipeline["downloaded"] == [] and fake_pipeline["loaded"] is None
    assert (processed / "dengue_rows_raw.csv").read_text() == before


def test_matching_uses_year_and_week_not_file_name(processed, fake_pipeline):
    # Old index written with an unpadded name must still count as "already have week 1".
    idx = pd.read_csv(processed / "reports_index.csv").assign(file="2026_week1.pdf")
    idx.to_csv(processed / "reports_index.csv", index=False)
    assert jobs.ingest_new_reports(refs=refs(1), processed_dir=processed, engine=object())["new_reports"] == 0


def test_needs_initial_pipeline(tmp_path):
    with pytest.raises(RuntimeError, match="run_data_pipeline"):
        jobs.ingest_new_reports(refs=[], processed_dir=tmp_path, engine=object())


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr(jobs.time, "sleep", lambda s: None)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise ConnectionError("reset by peer")
        return "ok"
    assert jobs._with_retry(flaky) == "ok" and len(attempts) == 3


# ---------------------------------------------------------------- job control

def test_same_job_never_runs_twice_at_once(monkeypatch):
    monkeypatch.setitem(jobs.JOBS, "daily_update", lambda: {"ran": True})
    assert jobs.run_job("daily_update") == {"ran": True}
    with jobs._locks["daily_update"]:                      # pretend it is already running
        assert jobs.run_job("daily_update") is None


def test_failed_job_does_not_crash(monkeypatch):
    def boom():
        raise RuntimeError("API down")
    monkeypatch.setitem(jobs.JOBS, "weekly_reports", boom)
    assert jobs.run_job("weekly_reports") is None
    assert not jobs._locks["weekly_reports"].locked()        # lock released after failure


def test_due_jobs(monkeypatch):
    now = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
    monkeypatch.setattr(jobs, "last_success", lambda engine=None: {
        "daily_update": now - timedelta(hours=30),     # overdue (> 20 h)
        "weekly_reports": now - timedelta(days=2),     # fine
        "monthly_retrain": None,                       # never ran
        "news_signals": now - timedelta(hours=1)})     # fine
    assert jobs.due_jobs(now) == ["daily_update", "monthly_retrain"]


@contextmanager
def fake_run(job_name, engine=None):
    yield {"rows": None, "message": None}


@pytest.mark.parametrize("old, new, accepted", [(0.17, 0.18, True), (0.17, 0.165, True), (0.17, 0.12, False),
                                                 (None, 0.10, True)])
def test_retrain_keeps_better_model(monkeypatch, old, new, accepted):
    import denguewatch.ml.data as data
    import denguewatch.ml.predict as predict
    import denguewatch.ml.train as train
    registered = []
    monkeypatch.setattr(jobs, "pipeline_run", fake_run)
    monkeypatch.setattr(jobs, "_active_pr_auc", lambda engine: old)
    monkeypatch.setattr(jobs, "_predict", lambda weeks_back, engine: 640)
    monkeypatch.setattr(data, "load_from_db", lambda engine: None)
    monkeypatch.setattr(train, "save_bundle", lambda bundle: "model.joblib")
    monkeypatch.setattr(predict, "register_model", lambda path, bundle, engine: registered.append(path) or 7)
    res = jobs.monthly_retrain(engine=object(), train=lambda inputs: {"metrics": {"test_pr_auc": new}})
    assert res["accepted"] is accepted
    assert registered == (["model.joblib"] if accepted else [])


# ---------------------------------------------------------------- scheduler

def test_scheduler_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    assert runner.start_scheduler() is None and runner.next_runs() == []


def test_scheduler_timetable(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    sched = runner.start_scheduler(catch_up_delay=-1)       # no catch-up in tests
    try:
        nxt = {r["job"]: r["next_run"] for r in runner.next_runs()}
        assert set(nxt) == {"daily_update", "weekly_reports", "monthly_retrain", "news_signals"}
        assert nxt["daily_update"].endswith("06:30+05:30")    # India time
        weekly = sched.get_job("weekly_reports").next_run_time
        assert weekly.weekday() == 0 and weekly.hour == 7        # Monday 07:00
        assert sched.get_job("monthly_retrain").next_run_time.day == 1
    finally:
        runner.stop_scheduler()


def test_catch_up_runs_overdue_jobs_in_order(monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "mark_interrupted", lambda: 0)
    monkeypatch.setattr(runner, "due_jobs", lambda: ["monthly_retrain", "daily_update", "weekly_reports"])
    monkeypatch.setattr(runner, "run_job", lambda name: ran.append(name))
    runner.catch_up()
    assert ran == ["weekly_reports", "daily_update", "monthly_retrain"]   # reports before scores, retrain last
