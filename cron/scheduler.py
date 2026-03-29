"""Cron scheduler — checks for due jobs and executes them via the agent API."""

import asyncio
import json
import logging
import os

import httpx

from cron.jobs import get_due_jobs, mark_job_run, save_job_output

log = logging.getLogger(__name__)

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080")


async def run_job(job: dict) -> tuple[bool, str]:
    """Execute a single cron job by calling the agent /chat API.

    Returns (success, response_text).
    """
    prompt = job["prompt"]
    model = job.get("model")

    payload = {
        "message": prompt,
        "role": "owner",
    }
    if model:
        payload["model"] = model

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{AGENT_URL}/chat",
                json=payload,
                timeout=300,
            )
            data = resp.json()
            response_text = data.get("response", "")
            return True, response_text
    except Exception as e:
        log.error("Cron job '%s' failed: %s", job["id"], e)
        return False, str(e)


async def tick():
    """Check for due jobs and execute them. Called periodically."""
    due = get_due_jobs()
    if not due:
        return

    log.info("Cron tick: %d jobs due", len(due))

    for job in due:
        job_id = job["id"]
        job_name = job.get("name", job_id)
        log.info("Running cron job '%s' (%s)", job_name, job_id)

        success, output = await run_job(job)

        # Save output
        save_job_output(job_id, output)

        # Mark job run
        mark_job_run(job_id, success, error=output if not success else None)

        # Deliver result
        deliver = job.get("deliver", "local")
        if deliver != "local" and success:
            await _deliver_result(job, output)

        log.info("Cron job '%s' %s", job_name, "completed" if success else "failed")


async def _deliver_result(job: dict, output: str):
    """Deliver cron job output to configured target."""
    deliver = job.get("deliver", "local")

    if deliver == "local":
        return  # Already saved to file

    # Deliver to gateway if it's a platform target
    # Format: "telegram", "telegram:chat_id", "slack:channel_id"
    gateway_url = os.environ.get("GATEWAY_URL")
    if not gateway_url:
        log.warning("Cannot deliver cron output — GATEWAY_URL not set")
        return

    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{gateway_url}/deliver",
                json={
                    "target": deliver,
                    "content": output[:4000],
                    "job_id": job["id"],
                    "job_name": job.get("name", ""),
                },
                timeout=30,
            )
    except Exception as e:
        log.error("Failed to deliver cron output: %s", e)


async def start_scheduler(interval: int = 60):
    """Start the scheduler loop. Runs tick() every interval seconds."""
    log.info("Cron scheduler started (interval=%ds)", interval)
    while True:
        try:
            await tick()
        except Exception as e:
            log.error("Cron tick failed: %s", e)
        await asyncio.sleep(interval)
