import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.sessions import SessionMiddleware

import asyncio

from . import auth, catalog, chat, cleanup, config, db, identity, scheduler, search, skills, telemetry, tips, uploads, workspaces


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    # Make the shared /files volume writable by both backend (root) and tools (uid 1000).
    # The named volume is created with root ownership on first mount; without this, the tools
    # container's generate_* endpoints fail with PermissionError when writing PDFs/xlsx/pptx.
    import os
    for d in (config.FILES_DIR,):
        try:
            os.makedirs(d, exist_ok=True)
            os.chmod(d, 0o777)
        except OSError:
            pass
    # Cron-driven prompt runs. In-process AsyncIOScheduler picks up every enabled
    # schedule from DB at boot and re-registers it. Stays in lifespan scope so
    # it shuts down cleanly on container stop.
    scheduler.init_scheduler()
    # Skills catalog: register the daily GitHub-discovery refresh, and do a
    # one-off boot refresh if the catalog is empty or stale so a fresh deploy
    # has skills to browse without waiting for the first cron fire.
    scheduler.register_catalog_refresh()
    asyncio.create_task(catalog.refresh_if_stale())
    # Privacy: sweep chats / files / workspace artifacts older than 24h.
    # Run once at boot so a cold start after downtime catches up immediately,
    # then hourly thereafter. NEVER in local desktop mode — there WORKSPACES_DIR
    # is the user's real home directory, and the sweep would delete their own
    # files. The 24h retention is a shared-server privacy control, not a personal
    # one; on a local install the user owns their data outright.
    cleanup_task = None
    if config.ATELIER_LOCAL:
        print("[cleanup] disabled in local mode (would target the user's home dir)", flush=True)
    else:
        try:
            cleanup.purge_expired_now()
        except Exception as e:
            print(f"[cleanup] boot sweep failed: {e}", flush=True)
        cleanup_task = asyncio.create_task(cleanup.cleanup_loop())
    try:
        yield
    finally:
        if cleanup_task is not None:
            cleanup_task.cancel()
        scheduler.shutdown_scheduler()


app = FastAPI(title="Family AI", lifespan=lifespan)

# Tracing — must be set up after the FastAPI app is created so the auto-instrumentor
# can hook the routes. No-ops cleanly when OTEL_TRACING=0 or no exporter is configured.
telemetry.init_tracing(app)

app.add_middleware(
    SessionMiddleware,
    secret_key=config.SESSION_SECRET,
    same_site="lax",
    https_only=config.PUBLIC_BACKEND_URL.startswith("https://"),
    session_cookie="famai_sid",
    max_age=60 * 60 * 24 * 30,  # 30 days
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.PUBLIC_FRONTEND_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth.router)
app.include_router(chat.router)
app.include_router(uploads.router)
app.include_router(workspaces.router)
# catalog MUST be included before skills: skills.py defines GET /skills/{sid},
# which otherwise shadows the literal /skills/catalog path (FastAPI matches routes
# in registration order, so the parametrized route would catch "catalog" as an id).
app.include_router(catalog.router)
app.include_router(skills.router)
app.include_router(identity.router)
app.include_router(search.router)
app.include_router(tips.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


# Desktop build: serve the statically-exported frontend from this same origin.
# Registered LAST so every API route above wins. Resolves Next export layout —
# exact file, `<path>.html`, or `<path>/index.html` — with an SPA fallback to
# index.html for unknown client routes (deep-link / hard reload).
if config.FRONTEND_DIST and os.path.isdir(config.FRONTEND_DIST):
    _DIST = Path(config.FRONTEND_DIST).resolve()

    @app.get("/{path:path}")
    def _serve_frontend(path: str):
        for cand in (_DIST / path, _DIST / f"{path}.html", _DIST / path / "index.html"):
            try:
                resolved = cand.resolve()
                resolved.relative_to(_DIST)          # block path traversal
            except (ValueError, OSError):
                continue
            if resolved.is_file():
                return FileResponse(resolved)
        # A missing asset is a real 404; an unknown route falls back to the SPA shell.
        last = path.rsplit("/", 1)[-1]
        if path.startswith("_next/") or "." in last:
            raise HTTPException(404)
        return FileResponse(_DIST / "index.html")

    print(f"[main] serving bundled frontend from {_DIST}", flush=True)
