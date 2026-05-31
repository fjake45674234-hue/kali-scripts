#!/usr/bin/env python3
"""
Missed-cron catchup: run any Hermes cron jobs whose scheduled window was
missed while the system was offline.

Called by hermes-cron-catchup.service immediately after the gateway starts.
Waits for the gateway to be ready, then triggers any job whose next_run_at
is in the past (i.e. the system was off when it was due).

The Hermes scheduler fast-forwards missed jobs after a 2-hour grace window
instead of running them — this script fires them before that happens.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERMES_HOME  = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
JOBS_FILE    = HERMES_HOME / "cron" / "jobs.json"
PID_FILE     = HERMES_HOME / "gateway.pid"
GATEWAY_WAIT = 30   # seconds to wait for gateway before giving up
POLL_INTERVAL = 1   # seconds between readiness checks


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def wait_for_gateway(timeout: int = GATEWAY_WAIT) -> bool:
    """Return True once the gateway PID file exists and the process is alive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if PID_FILE.exists():
            try:
                raw = PID_FILE.read_text().strip()
                # PID file may be a plain int or JSON {"pid": N, ...}
                try:
                    data = json.loads(raw)
                    pid = int(data["pid"])
                except (json.JSONDecodeError, KeyError):
                    pid = int(raw)
                if _pid_alive(pid):
                    return True
            except (ValueError, OSError):
                pass
        time.sleep(POLL_INTERVAL)
    return False


def load_jobs() -> list:
    if not JOBS_FILE.exists():
        return []
    return json.loads(JOBS_FILE.read_text()).get("jobs", [])


def find_missed(jobs: list) -> list:
    """Return jobs that are enabled, non-paused, and whose next_run_at is in the past."""
    now = datetime.now(timezone.utc)
    missed = []
    for job in jobs:
        if not job.get("enabled", True):
            continue
        if job.get("state") == "paused":
            continue
        schedule = job.get("schedule", {})
        if schedule.get("kind") not in ("cron", "interval"):
            continue     # one-shot jobs handle themselves

        next_run = job.get("next_run_at")
        if not next_run:
            continue

        try:
            nrt = datetime.fromisoformat(next_run)
            if nrt.tzinfo is None:
                nrt = nrt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        if nrt < now:
            missed.append(job)

    return missed


def trigger(job: dict) -> bool:
    job_id   = job["id"]
    job_name = job.get("name", job_id)
    try:
        result = subprocess.run(
            ["hermes", "cron", "run", "--accept-hooks", job_id],
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "HERMES_ACCEPT_HOOKS": "1"},
        )
        if result.returncode == 0:
            print(f"  [ok]   {job_name} ({job_id})")
            return True
        else:
            print(f"  [fail] {job_name} ({job_id}): {result.stderr.strip()}")
            return False
    except Exception as exc:
        print(f"  [err]  {job_name} ({job_id}): {exc}")
        return False


def main() -> int:
    print(f"[cron-catchup] Waiting for Hermes gateway (up to {GATEWAY_WAIT}s)...")
    if not wait_for_gateway():
        print("[cron-catchup] Gateway did not start in time — aborting.")
        return 1

    # Small extra sleep so the scheduler thread is initialised and the
    # first tick hasn't run yet (scheduler waits ~60s after start).
    time.sleep(3)

    jobs   = load_jobs()
    missed = find_missed(jobs)

    if not missed:
        print("[cron-catchup] No missed jobs — all up to date.")
        return 0

    print(f"[cron-catchup] {len(missed)} missed job(s) detected — triggering:")
    for job in missed:
        sched = job.get("schedule", {})
        nrt   = job.get("next_run_at", "?")
        print(f"    {job.get('name', job['id'])}  (was due {nrt}, kind={sched.get('kind')})")

    ok = sum(trigger(j) for j in missed)
    print(f"[cron-catchup] Done — {ok}/{len(missed)} triggered successfully.")
    return 0 if ok == len(missed) else 1


if __name__ == "__main__":
    sys.exit(main())
