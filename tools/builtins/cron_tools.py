"""Cron tools — agent-facing tool for scheduling and managing cron jobs."""

import json
import logging

from cron.jobs import (
    create_job, list_jobs, get_job, update_job,
    pause_job, resume_job, remove_job,
)

log = logging.getLogger(__name__)


async def cronjob(args: dict) -> str:
    """Unified cron job management tool."""
    action = args.get("action", "")

    if action == "create":
        prompt = args.get("prompt", "")
        schedule = args.get("schedule", "")
        if not prompt or not schedule:
            return json.dumps({"error": "prompt and schedule are required"})

        try:
            job = create_job(
                prompt=prompt,
                schedule=schedule,
                name=args.get("name", ""),
                repeat=args.get("repeat"),
                deliver=args.get("deliver", "local"),
                model=args.get("model"),
            )
            return json.dumps({"status": "created", "job": job})
        except ValueError as e:
            return json.dumps({"error": str(e)})

    elif action == "list":
        jobs = list_jobs(include_disabled=args.get("include_disabled", False))
        return json.dumps({"jobs": jobs})

    elif action == "get":
        job_id = args.get("job_id", "")
        job = get_job(job_id)
        if not job:
            return json.dumps({"error": f"job '{job_id}' not found"})
        return json.dumps({"job": job})

    elif action == "pause":
        job_id = args.get("job_id", "")
        reason = args.get("reason", "")
        if pause_job(job_id, reason):
            return json.dumps({"status": "paused", "job_id": job_id})
        return json.dumps({"error": f"job '{job_id}' not found"})

    elif action == "resume":
        job_id = args.get("job_id", "")
        if resume_job(job_id):
            return json.dumps({"status": "resumed", "job_id": job_id})
        return json.dumps({"error": f"job '{job_id}' not found"})

    elif action == "remove":
        job_id = args.get("job_id", "")
        if remove_job(job_id):
            return json.dumps({"status": "removed", "job_id": job_id})
        return json.dumps({"error": f"job '{job_id}' not found"})

    else:
        return json.dumps({"error": f"unknown action: {action}. Use: create, list, get, pause, resume, remove"})


def register(registry):
    """Register cron tools."""
    registry.register(
        name="cronjob",
        description=(
            "Schedule and manage recurring agent tasks. "
            "Actions: create (with prompt + schedule), list, get, pause, resume, remove. "
            "Schedule formats: '30m' (one-shot), 'every 2h' (recurring), '0 9 * * *' (cron)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "get", "pause", "resume", "remove"],
                    "description": "Action to perform",
                },
                "prompt": {"type": "string", "description": "Task prompt (for create)"},
                "schedule": {"type": "string", "description": "Schedule expression (for create)"},
                "name": {"type": "string", "description": "Job name (optional)"},
                "job_id": {"type": "string", "description": "Job ID (for get/pause/resume/remove)"},
                "repeat": {"type": "integer", "description": "Number of times to repeat (null = forever)"},
                "deliver": {"type": "string", "description": "Delivery target: 'local', 'telegram', 'slack:channel'", "default": "local"},
                "model": {"type": "string", "description": "Override model for this job"},
                "reason": {"type": "string", "description": "Pause reason"},
                "include_disabled": {"type": "boolean", "description": "Include disabled jobs in list", "default": False},
            },
            "required": ["action"],
        },
        handler=cronjob,
        toolset="cron",
        emoji="⏰",
    )
