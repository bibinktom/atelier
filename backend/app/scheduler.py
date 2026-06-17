"""In-process AsyncIOScheduler driving cron-fired prompt runs.

Each scheduled fire creates a NEW conversation (titled "<name> — <date>") and
posts the schedule's prompt as the user message, then drives one full chat turn.
The user sees the new conversation in their sidebar next time they open the app.

State lives in `schedules` (DB). On boot, `init_scheduler` re-registers every
enabled schedule. Adding/removing schedules at runtime touches both DB and the
in-memory APScheduler via `register_schedule` / `unregister_schedule`.
"""
from __future__ import annotations

import datetime
import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config, db

log = logging.getLogger("atelier.scheduler")

_scheduler: AsyncIOScheduler | None = None


def parse_cron(expr: str) -> CronTrigger:
    """Validate + build a CronTrigger from a 5-field cron expression
    ('M H DOM MON DOW'). Raises ValueError on bad input."""
    try:
        return CronTrigger.from_crontab(expr)
    except Exception as e:
        raise ValueError(f"invalid cron expression {expr!r}: {e}") from e


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler is None:
        raise RuntimeError("scheduler not initialised — call init_scheduler() first")
    return _scheduler


def init_scheduler() -> AsyncIOScheduler:
    """Build the AsyncIOScheduler, register all enabled schedules from DB,
    and start it. Safe to call once at app startup."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    _scheduler = AsyncIOScheduler(timezone="UTC")
    for sch in db.list_all_enabled_schedules():
        try:
            _register(_scheduler, sch)
        except Exception as e:  # noqa: BLE001
            log.warning("failed to register schedule %s (%s): %s", sch["id"], sch["name"], e)
    _scheduler.start()
    log.info("scheduler started with %d job(s)", len(_scheduler.get_jobs()))
    return _scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        pass
    _scheduler = None


_CATALOG_JOB_ID = "catalog:refresh"


def register_catalog_refresh() -> None:
    """Register the daily skills-catalog GitHub refresh as an in-process cron job.
    No-op when the catalog is disabled or the cron expression is invalid."""
    if not config.SKILLS_CATALOG_ENABLED:
        return
    sched = get_scheduler()
    try:
        trigger = parse_cron(config.SKILLS_CATALOG_CRON)
    except ValueError as e:
        log.warning("catalog: bad SKILLS_CATALOG_CRON %r: %s", config.SKILLS_CATALOG_CRON, e)
        return
    sched.add_job(
        _run_catalog_refresh,
        trigger=trigger,
        id=_CATALOG_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,  # if backend was down at fire time, still run within the hour
        coalesce=True,
    )
    log.info("catalog: daily refresh registered (cron %r)", config.SKILLS_CATALOG_CRON)


async def _run_catalog_refresh() -> None:
    # Local import to avoid a circular at module load (catalog imports skills→db→…).
    from . import catalog as catalog_module
    try:
        await catalog_module.refresh_catalog()
    except Exception:  # noqa: BLE001
        log.exception("catalog: scheduled refresh failed")


def register_schedule(sch: dict) -> None:
    """Add or replace the in-memory job for `sch`. DB row should already be
    persisted (or about to be)."""
    sched = get_scheduler()
    _register(sched, sch)


def unregister_schedule(schedule_id: str) -> None:
    sched = get_scheduler()
    try:
        sched.remove_job(_job_id(schedule_id))
    except Exception:  # noqa: BLE001
        pass


def _job_id(schedule_id: str) -> str:
    return f"sched:{schedule_id}"


def _register(sched: AsyncIOScheduler, sch: dict) -> None:
    if not sch.get("enabled"):
        return
    trigger = parse_cron(sch["cron_expr"])
    sched.add_job(
        _run_scheduled,
        trigger=trigger,
        args=[sch["id"]],
        id=_job_id(sch["id"]),
        replace_existing=True,
        misfire_grace_time=60,  # if backend was offline, run jobs <60s late, drop older
        coalesce=True,           # collapse multiple missed fires into one
    )


async def _run_scheduled(schedule_id: str) -> None:
    """The job body. Loaded by APScheduler at fire time. Looks up the schedule
    fresh from DB on every fire so renames/prompt edits take effect without
    re-registering."""
    # Local import to avoid a circular: chat.py imports config, which is OK,
    # but importing chat at module load would pull in tools_client → DB → us.
    from . import chat as chat_module

    sch = None
    try:
        with _conn_get_schedule(schedule_id) as row:
            if row:
                sch = dict(row)
    except Exception as e:  # noqa: BLE001
        log.exception("scheduler: cannot load schedule %s: %s", schedule_id, e)
        return
    if not sch or not sch.get("enabled"):
        log.info("scheduler: schedule %s gone or disabled, skipping fire", schedule_id)
        return

    user = db.get_user(sch["user_id"])
    if not user:
        log.warning("scheduler: schedule %s has no user, disabling", schedule_id)
        db.update_schedule_status(schedule_id, last_error="user not found")
        return

    model = sch.get("model") or config.DEFAULT_MODEL
    today = datetime.date.today().isoformat()
    title = f"{sch['name']} — {today}"[:60]

    # Each fire creates a fresh conversation so the synthetic user prompt
    # doesn't muddle an existing chat thread.
    conv = db.create_conversation(user_id=user["id"], model=model, title=title)
    cid = conv["id"]

    log.info("scheduler: fire schedule %s for user %s -> conv %s",
             schedule_id, user["id"], cid)

    try:
        body = chat_module.PostMessageBody(content=sch["prompt_text"], model=model)
        async for _ev in chat_module.run_turn(cid=cid, body=body, user=user):
            # Discard SSE events — we only care about the DB side-effects.
            pass
        db.update_schedule_status(
            schedule_id,
            last_run_at=int(time.time()),
            last_conversation_id=cid,
            clear_error=True,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("scheduler: schedule %s fire failed", schedule_id)
        db.update_schedule_status(
            schedule_id,
            last_run_at=int(time.time()),
            last_conversation_id=cid,
            last_error=f"{type(e).__name__}: {e}"[:500],
        )


# Use the same connect helper as db.py so the schedule load uses one Row.
from contextlib import contextmanager


@contextmanager
def _conn_get_schedule(schedule_id: str):
    with db.connect() as c:
        row = c.execute("SELECT * FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
        yield row
