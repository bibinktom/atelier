"""Skills catalog — a shared, browsable directory of Claude-style SKILL.md files
discovered on public GitHub, refreshed once a day.

Flow:
  • `refresh_catalog()` searches GitHub repositories (sorted by stars), walks each
    repo's git tree for files named SKILL.md, fetches them from the raw host
    (which does NOT cost API rate-limit), parses the front-matter, and upserts the
    result into the global `catalog_skills` table. Stale rows (not seen this run)
    are pruned.
  • Users browse the catalog (`GET /skills/catalog`) and install a row
    (`POST /skills/catalog/{id}/install`), which copies it into their own `skills`.

By default we use the *unauthenticated* GitHub API (~60 core req/hr) which is
ample for a once-daily fan-out of ~30 repos. Set `GITHUB_TOKEN` to raise the
ceiling. The token is sent ONLY to api.github.com — never to any model endpoint.

The daily cron is registered by `scheduler.init_scheduler`; this module just
exposes `refresh_catalog()` plus the HTTP routes.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from . import config, db
from .auth import require_admin, require_approved_user as require_user
from .skills import _parse_frontmatter

log = logging.getLogger("atelier.catalog")

router = APIRouter()

_GH_API = "https://api.github.com"
_GH_RAW = "https://raw.githubusercontent.com"
_UA = "Atelier-Skills-Catalog/1.0 (+https://github.com)"

# Only one refresh runs at a time (boot + cron + manual could otherwise overlap).
_refresh_lock = asyncio.Lock()
_refreshing = False


def _headers() -> dict[str, str]:
    h = {"User-Agent": _UA, "Accept": "application/vnd.github+json"}
    if config.GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
    return h


def _derive_prompt(name: str, body: str, meta: dict) -> str:
    """Best-effort trigger prompt, mirroring skills.upload_skill's heuristic."""
    template = (meta.get("prompt") or meta.get("trigger") or "").strip()
    if not template:
        for para in body.split("\n\n"):
            p = para.strip()
            if p and not p.startswith("#"):
                template = p[:1000]
                break
    return template or name


async def _search_repos(client: httpx.AsyncClient) -> list[dict]:
    """Run each configured repo-search query, dedup by full_name, keep top repos
    by stars. Returns repo dicts with the fields we need downstream."""
    seen: dict[str, dict] = {}
    per_page = max(5, min(50, config.SKILLS_CATALOG_MAX_REPOS))
    for q in config.SKILLS_CATALOG_QUERIES:
        try:
            r = await client.get(
                f"{_GH_API}/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": per_page},
            )
            if r.status_code == 403:
                log.warning("catalog: GitHub rate-limited on query %r (set GITHUB_TOKEN to raise the limit)", q)
                continue
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            log.warning("catalog: repo search failed for %r: %s", q, e)
            continue
        for item in r.json().get("items", []):
            full = item.get("full_name")
            if not full or full in seen:
                continue
            seen[full] = {
                "full_name": full,
                "html_url": item.get("html_url"),
                "default_branch": item.get("default_branch") or "main",
                "stars": int(item.get("stargazers_count") or 0),
                "owner": (item.get("owner") or {}).get("login"),
                "license": (item.get("license") or {}).get("spdx_id"),
            }
        # The unauthenticated search endpoint is the tightest limit (10 req/min);
        # space the queries out a little so a 3-query config never trips it.
        await asyncio.sleep(0.5)
    repos = sorted(seen.values(), key=lambda d: d["stars"], reverse=True)
    return repos[: config.SKILLS_CATALOG_MAX_REPOS]


async def _skill_paths(client: httpx.AsyncClient, repo: dict) -> list[str]:
    """Return paths of files named SKILL.md in the repo's default-branch tree."""
    url = f"{_GH_API}/repos/{repo['full_name']}/git/trees/{repo['default_branch']}"
    try:
        r = await client.get(url, params={"recursive": "1"})
        if r.status_code == 403:
            log.warning("catalog: rate-limited walking tree of %s", repo["full_name"])
            return []
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("catalog: tree fetch failed for %s: %s", repo["full_name"], e)
        return []
    out = []
    for node in r.json().get("tree", []):
        if node.get("type") == "blob" and node.get("path", "").rsplit("/", 1)[-1].lower() == "skill.md":
            out.append(node["path"])
            if len(out) >= config.SKILLS_CATALOG_MAX_FILES_PER_REPO:
                break
    return out


async def _ingest_skill(client: httpx.AsyncClient, repo: dict, path: str) -> bool:
    """Fetch one SKILL.md from the raw host, parse it, upsert. Returns True if stored."""
    raw_url = f"{_GH_RAW}/{repo['full_name']}/{repo['default_branch']}/{path}"
    try:
        r = await client.get(raw_url)
        r.raise_for_status()
        text = r.text
    except Exception as e:  # noqa: BLE001
        log.debug("catalog: raw fetch failed %s: %s", raw_url, e)
        return False
    if len(text) > 200_000:
        text = text[:200_000]

    meta, body = _parse_frontmatter(text)
    name = (meta.get("name") or "").strip()
    if not name:
        # Fall back to the parent directory name (Anthropic's layout is <skill>/SKILL.md).
        parent = path.rsplit("/", 2)[-2] if "/" in path else repo["full_name"].split("/")[-1]
        name = parent.replace("_", " ").replace("-", " ").strip()
    if not name or not body:
        return False

    description = (meta.get("description") or "").strip() or None
    source_url = f"{repo['html_url']}/blob/{repo['default_branch']}/{path}"
    db.upsert_catalog_skill(
        source_url=source_url,
        repo=repo["full_name"],
        repo_url=repo["html_url"],
        author=repo.get("owner"),
        name=name[:64],
        description=description[:240] if description else None,
        body_md=body,
        prompt_template=_derive_prompt(name, body, meta)[:4000],
        stars=repo["stars"],
        license=repo.get("license"),
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    return True


async def refresh_catalog() -> dict:
    """Discover SKILL.md files across GitHub and refresh the shared catalog.
    Idempotent and safe to call concurrently — overlapping calls are serialized."""
    global _refreshing
    if not config.SKILLS_CATALOG_ENABLED:
        return {"ok": False, "reason": "disabled"}
    async with _refresh_lock:
        _refreshing = True
        started = int(time.time())
        stored = 0
        try:
            timeout = httpx.Timeout(connect=10.0, read=30.0, write=15.0, pool=10.0)
            async with httpx.AsyncClient(timeout=timeout, headers=_headers(), follow_redirects=True) as client:
                repos = await _search_repos(client)
                log.info("catalog: refreshing from %d repo(s)", len(repos))
                for repo in repos:
                    if stored >= config.SKILLS_CATALOG_MAX_SKILLS:
                        break
                    paths = await _skill_paths(client, repo)
                    # Diversity guard: never let one mega-repo flood the catalog.
                    from_repo = 0
                    for path in paths:
                        if stored >= config.SKILLS_CATALOG_MAX_SKILLS:
                            break
                        if from_repo >= config.SKILLS_CATALOG_MAX_PER_REPO:
                            break
                        try:
                            if await _ingest_skill(client, repo, path):
                                stored += 1
                                from_repo += 1
                        except Exception:  # noqa: BLE001
                            log.exception("catalog: ingest failed for %s/%s", repo["full_name"], path)
            # Drop anything we didn't re-see this run (deleted/renamed upstream).
            pruned = db.prune_catalog_stale(started)
            log.info("catalog: refresh done — %d stored, %d pruned, %d total",
                     stored, pruned, db.count_catalog_skills())
            return {"ok": True, "stored": stored, "pruned": pruned, "total": db.count_catalog_skills()}
        finally:
            _refreshing = False


async def refresh_if_stale(max_age_seconds: int = 24 * 3600) -> None:
    """Boot helper: refresh only if the catalog is empty or older than max_age."""
    if not config.SKILLS_CATALOG_ENABLED:
        return
    last = db.catalog_last_refreshed()
    if last is not None and (int(time.time()) - last) < max_age_seconds:
        return
    try:
        await refresh_catalog()
    except Exception:  # noqa: BLE001
        log.exception("catalog: boot refresh failed")


# ---------- routes ----------

def _public_row(row: dict) -> dict:
    """Trim a catalog row for the browse list (omit the full body)."""
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "repo": row["repo"],
        "repo_url": row["repo_url"],
        "source_url": row["source_url"],
        "author": row["author"],
        "stars": row["stars"],
        "license": row["license"],
        "install_count": row["install_count"],
    }


@router.get("/skills/catalog")
async def browse_catalog(_: Request, q: str | None = None, user=Depends(require_user)):
    rows = db.list_catalog_skills(query=q)
    return {
        "skills": [_public_row(r) for r in rows],
        "count": db.count_catalog_skills(),
        "last_refreshed": db.catalog_last_refreshed(),
        "refreshing": _refreshing,
        "enabled": config.SKILLS_CATALOG_ENABLED,
    }


@router.post("/skills/catalog/{cid}/install")
async def install_catalog_skill(cid: str, _: Request, user=Depends(require_user)):
    """Copy a catalog skill into the requesting user's own library."""
    rec = db.get_catalog_skill(cid)
    if not rec:
        raise HTTPException(404, "catalog skill not found")
    dup = db.find_duplicate_skill(user["id"], rec["name"])
    if dup:
        raise HTTPException(409, f"you already have a similar skill: {dup['name']!r}")
    skill = db.add_skill(
        user["id"],
        name=rec["name"],
        description=rec["description"],
        prompt_template=rec["prompt_template"] or rec["name"],
        body_md=rec["body_md"],
        is_suggested=False,
    )
    db.bump_catalog_install(cid)
    return skill


@router.post("/skills/catalog/refresh")
async def trigger_refresh(_: Request, admin=Depends(require_admin)):
    """Admin-only: kick a refresh now (runs in the background)."""
    if not config.SKILLS_CATALOG_ENABLED:
        raise HTTPException(400, "skills catalog is disabled")
    asyncio.create_task(refresh_catalog())
    return {"ok": True, "started": True, "refreshing": True}
