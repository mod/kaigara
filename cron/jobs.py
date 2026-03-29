"""Cron job storage and management."""

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)

JOBS_DIR = Path(os.environ.get("KAIGARA_DATA_DIR", str(Path.home() / ".kaigara"))) / "cron"
JOBS_FILE = JOBS_DIR / "jobs.json"
OUTPUT_DIR = JOBS_DIR / "output"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_jobs() -> list[dict]:
    """Load all jobs from disk."""
    if not JOBS_FILE.exists():
        return []
    try:
        return json.loads(JOBS_FILE.read_text())
    except Exception as e:
        log.error("Failed to load jobs: %s", e)
        return []


def _save_jobs(jobs: list[dict]):
    """Save all jobs to disk."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2, default=str))


def parse_schedule(schedule_str: str) -> dict:
    """Parse a schedule string into a structured dict.

    Formats:
    - "30m", "2h", "1d" — one-shot delay
    - "every 30m", "every 2h" — recurring interval
    - "0 9 * * *" — cron expression
    - ISO timestamp — one-shot at specific time
    """
    schedule_str = schedule_str.strip()

    # Recurring interval
    if schedule_str.startswith("every "):
        duration = _parse_duration(schedule_str[6:])
        if duration:
            return {"type": "interval", "minutes": duration}

    # Cron expression (5 fields)
    parts = schedule_str.split()
    if len(parts) == 5 and all(_looks_like_cron_field(p) for p in parts):
        return {"type": "cron", "expression": schedule_str}

    # One-shot delay
    duration = _parse_duration(schedule_str)
    if duration:
        return {"type": "once", "minutes": duration}

    # ISO timestamp
    try:
        dt = datetime.fromisoformat(schedule_str)
        return {"type": "once_at", "at": dt.isoformat()}
    except ValueError:
        pass

    raise ValueError(f"Cannot parse schedule: {schedule_str}")


def _parse_duration(s: str) -> int | None:
    """Parse '30m', '2h', '1d' into minutes."""
    s = s.strip().lower()
    if s.endswith("m") and s[:-1].isdigit():
        return int(s[:-1])
    if s.endswith("h") and s[:-1].isdigit():
        return int(s[:-1]) * 60
    if s.endswith("d") and s[:-1].isdigit():
        return int(s[:-1]) * 1440
    return None


def _looks_like_cron_field(s: str) -> bool:
    return bool(s) and all(c in "0123456789*,/-" for c in s)


def compute_next_run(schedule: dict, from_time: datetime | None = None) -> datetime | None:
    """Compute next run time for a schedule."""
    from_time = from_time or _now()

    if schedule["type"] == "once":
        from datetime import timedelta
        return from_time + timedelta(minutes=schedule["minutes"])

    if schedule["type"] == "once_at":
        return datetime.fromisoformat(schedule["at"])

    if schedule["type"] == "interval":
        from datetime import timedelta
        return from_time + timedelta(minutes=schedule["minutes"])

    if schedule["type"] == "cron":
        try:
            from croniter import croniter
            cron = croniter(schedule["expression"], from_time)
            return cron.get_next(datetime)
        except ImportError:
            log.error("croniter not installed — cannot use cron expressions")
            return None

    return None


def create_job(
    prompt: str,
    schedule: str,
    *,
    name: str = "",
    repeat: int | None = None,
    deliver: str = "local",
    origin: dict | None = None,
    model: str | None = None,
) -> dict:
    """Create a new cron job."""
    parsed_schedule = parse_schedule(schedule)
    next_run = compute_next_run(parsed_schedule)

    job = {
        "id": uuid4().hex[:12],
        "name": name or prompt[:50],
        "prompt": prompt,
        "model": model,
        "schedule": parsed_schedule,
        "schedule_display": schedule,
        "repeat": {"times": repeat, "completed": 0} if repeat else {"times": None, "completed": 0},
        "enabled": True,
        "state": "scheduled",
        "created_at": _now().isoformat(),
        "next_run_at": next_run.isoformat() if next_run else None,
        "last_run_at": None,
        "last_status": None,
        "last_error": None,
        "deliver": deliver,
        "origin": origin,
    }

    jobs = _load_jobs()
    jobs.append(job)
    _save_jobs(jobs)

    log.info("Created cron job '%s' (%s) — next run: %s", job["name"], job["id"], job["next_run_at"])
    return job


def list_jobs(include_disabled: bool = False) -> list[dict]:
    """List all jobs."""
    jobs = _load_jobs()
    if not include_disabled:
        jobs = [j for j in jobs if j.get("enabled", True)]
    return jobs


def get_job(job_id: str) -> dict | None:
    jobs = _load_jobs()
    return next((j for j in jobs if j["id"] == job_id), None)


def update_job(job_id: str, **updates) -> dict | None:
    """Update job fields."""
    jobs = _load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job.update(updates)
            if "schedule" in updates:
                job["next_run_at"] = compute_next_run(updates["schedule"]).isoformat()
            _save_jobs(jobs)
            return job
    return None


def pause_job(job_id: str, reason: str = "") -> bool:
    jobs = _load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job["enabled"] = False
            job["state"] = "paused"
            _save_jobs(jobs)
            return True
    return False


def resume_job(job_id: str) -> bool:
    jobs = _load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job["enabled"] = True
            job["state"] = "scheduled"
            job["next_run_at"] = compute_next_run(job["schedule"]).isoformat()
            _save_jobs(jobs)
            return True
    return False


def remove_job(job_id: str) -> bool:
    jobs = _load_jobs()
    new_jobs = [j for j in jobs if j["id"] != job_id]
    if len(new_jobs) == len(jobs):
        return False
    _save_jobs(new_jobs)
    return True


def mark_job_run(job_id: str, success: bool, error: str | None = None):
    """Update job after execution."""
    jobs = _load_jobs()
    for job in jobs:
        if job["id"] == job_id:
            job["last_run_at"] = _now().isoformat()
            job["last_status"] = "ok" if success else "error"
            job["last_error"] = error

            # Increment completed count
            job["repeat"]["completed"] = job["repeat"].get("completed", 0) + 1

            # Check if repeat limit reached
            times = job["repeat"].get("times")
            if times and job["repeat"]["completed"] >= times:
                job["state"] = "completed"
                job["enabled"] = False
                job["next_run_at"] = None
            elif job["schedule"]["type"] in ("interval", "cron"):
                job["next_run_at"] = compute_next_run(job["schedule"]).isoformat()
            else:
                # One-shot completed
                job["state"] = "completed"
                job["enabled"] = False
                job["next_run_at"] = None

            _save_jobs(jobs)
            return
    log.warning("Job %s not found for mark_job_run", job_id)


def get_due_jobs() -> list[dict]:
    """Get jobs that are due to run now."""
    now = _now()
    jobs = _load_jobs()
    due = []
    for job in jobs:
        if not job.get("enabled"):
            continue
        next_run = job.get("next_run_at")
        if not next_run:
            continue
        if datetime.fromisoformat(next_run) <= now:
            due.append(job)
    return due


def save_job_output(job_id: str, output: str):
    """Save job output to file."""
    job_dir = OUTPUT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    ts = _now().strftime("%Y%m%d_%H%M%S")
    (job_dir / f"{ts}.md").write_text(output)
