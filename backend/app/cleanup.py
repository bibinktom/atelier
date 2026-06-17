"""24-hour retention sweep.

Privacy guarantee: every artifact tied to a user (chats, generated files,
uploaded images, agent-written workspace files) is purged 24h after creation.
Nothing here is reversible — by design.
"""
import asyncio
import os
import time
from pathlib import Path

from . import config, db

RETENTION_SECONDS = 24 * 3600
INTERVAL_SECONDS = 3600  # sweep hourly


def _purge_db_and_files() -> dict:
    cutoff = int(time.time()) - RETENTION_SECONDS
    stats = {"conversations": 0, "files": 0, "workspace_files": 0, "workspace_dirs": 0}

    with db.connect() as c:
        old_files = c.execute(
            "SELECT id, path FROM files WHERE created_at < ?", (cutoff,)
        ).fetchall()
        for row in old_files:
            p = row["path"]
            if p and os.path.isfile(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass
        if old_files:
            ids = [r["id"] for r in old_files]
            placeholders = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM files WHERE id IN ({placeholders})", ids)
            stats["files"] = len(old_files)

        # Conversations cascade to messages (FK ON DELETE CASCADE) and to
        # conversation_skills / tasks. Files set their conversation_id to NULL
        # rather than cascade, which is fine — those file rows have already
        # been picked up above (or will be on their own age).
        cur = c.execute("DELETE FROM conversations WHERE updated_at < ?", (cutoff,))
        stats["conversations"] = cur.rowcount or 0

    return stats


def _purge_workspace_files(stats: dict) -> None:
    root = Path(config.WORKSPACES_DIR)
    if not root.is_dir():
        return
    cutoff = time.time() - RETENTION_SECONDS

    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for proj_dir in user_dir.iterdir():
            if not proj_dir.is_dir():
                continue
            # Bottom-up walk so we can prune empty dirs after their files go.
            for current, dirnames, filenames in os.walk(proj_dir, topdown=False):
                cur_path = Path(current)
                for fn in filenames:
                    fp = cur_path / fn
                    try:
                        if fp.stat().st_mtime < cutoff:
                            fp.unlink()
                            stats["workspace_files"] += 1
                    except OSError:
                        pass
                # Don't remove the project root itself — keep workspaces alive
                # so the agent can keep using them.
                if cur_path != proj_dir:
                    try:
                        cur_path.rmdir()
                        stats["workspace_dirs"] += 1
                    except OSError:
                        pass  # not empty, or permission, etc.


def purge_expired_now() -> dict:
    stats = _purge_db_and_files()
    _purge_workspace_files(stats)
    return stats


async def cleanup_loop() -> None:
    while True:
        try:
            stats = purge_expired_now()
            if any(stats.values()):
                print(f"[cleanup] purged: {stats}", flush=True)
        except Exception as e:
            print(f"[cleanup] failed: {e}", flush=True)
        await asyncio.sleep(INTERVAL_SECONDS)
