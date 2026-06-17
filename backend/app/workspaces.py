"""Project folder ('workspace') management.

Each user has one or more named workspaces. Each maps to a real directory on the
host (bind-mounted into the tools container at /workspaces/<user_id>/<slug>).
Conversations attach to a workspace; the model's workspace_* tool calls operate
inside that directory.
"""
import mimetypes
import os
import re
import shutil

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import config, db, preview
from .auth import require_approved_user as require_user

router = APIRouter()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return (s or "project")[:48]


def _workspace_dir(user_id: str, slug: str) -> str:
    return os.path.join(config.WORKSPACES_DIR, user_id, slug)


def _ensure_dir_user_writable(path: str) -> None:
    """Create the dir and best-effort hand it to host uid:gid 1000:1000 with mode 0777
    so the family member can edit those files from their normal file manager."""
    os.makedirs(path, exist_ok=True)
    try:
        os.chown(path, 1000, 1000)
    except (OSError, PermissionError):
        pass
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass


class CreateWorkspaceBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


@router.get("/workspaces")
async def list_user_workspaces(_: Request, user=Depends(require_user)):
    items = db.list_workspaces(user["id"])
    if not items:
        items = [db.ensure_default_workspace(user["id"])]
        try:
            _ensure_dir_user_writable(_workspace_dir(user["id"], items[0]["slug"]))
        except OSError:
            pass
    return {"workspaces": items}


def _user_root(user_id: str) -> str:
    return os.path.join(config.WORKSPACES_DIR, user_id)


def _dir_size(path: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for fn in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, fn)).st_size
            except OSError:
                continue
    return total


@router.get("/workspaces/usage")
async def workspace_usage(_: Request, user=Depends(require_user)):
    """Total disk used across all of this user's project folders, vs their quota."""
    root = _user_root(user["id"])
    used = _dir_size(root) if os.path.isdir(root) else 0
    quota = config.USER_QUOTA_BYTES
    return {"used": used, "quota": quota, "percent": round(100 * used / quota, 1) if quota else 0}


@router.post("/workspaces")
async def create_user_workspace(body: CreateWorkspaceBody, _: Request, user=Depends(require_user)):
    name = body.name.strip()
    base_slug = _slugify(name)
    # find a unique slug for this user
    slug = base_slug
    n = 2
    while db.get_workspace_by_slug(user["id"], slug):
        slug = f"{base_slug}-{n}"; n += 1
    rec = db.create_workspace(user["id"], name=name, slug=slug)
    try:
        _ensure_dir_user_writable(_workspace_dir(user["id"], slug))
    except OSError as e:
        # Roll back the DB row if the directory can't be created — better to fail loudly than create
        # an orphan workspace the model will then try to write into.
        db.delete_workspace(rec["id"], user["id"])
        raise HTTPException(500, f"could not create project folder: {e}")
    return rec


# ---- file browser ----

MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50MB per file


def _safe_rel_path(rel: str) -> str:
    """Normalise a user-provided relative path. Reject anything that escapes the workspace."""
    rel = (rel or "").strip()
    if rel in ("", ".", "/"):
        return ""
    # Strip leading slashes and disallow drive letters or `..` components.
    rel = rel.lstrip("/").lstrip("\\")
    parts = []
    for seg in rel.replace("\\", "/").split("/"):
        if not seg or seg == ".":
            continue
        if seg == "..":
            raise HTTPException(400, "path escapes workspace")
        if any(ch in seg for ch in ('\x00',)):
            raise HTTPException(400, "invalid path")
        parts.append(seg)
    return "/".join(parts)


def _resolve(user_id: str, ws: dict, rel: str) -> str:
    """Return absolute server-side path for a user/workspace/relative path. Verified to stay inside the workspace."""
    rel = _safe_rel_path(rel)
    root = os.path.realpath(_workspace_dir(user_id, ws["slug"]))
    target = os.path.realpath(os.path.join(root, rel))
    if not (target == root or target.startswith(root + os.sep)):
        raise HTTPException(400, "path escapes workspace")
    return target


@router.get("/workspaces/{wid}/files")
async def list_workspace_files(wid: str, _: Request,
                                path: str = Query("", description="Relative path inside the workspace"),
                                user=Depends(require_user)):
    ws = db.get_workspace(wid, user["id"])
    if not ws:
        raise HTTPException(404, "not found")
    target = _resolve(user["id"], ws, path)
    if not os.path.exists(target):
        # Lazily create the workspace dir on first browse.
        _ensure_dir_user_writable(_workspace_dir(user["id"], ws["slug"]))
        target = _resolve(user["id"], ws, path)
    if os.path.isfile(target):
        return {"path": path, "type": "file", "size": os.path.getsize(target)}
    entries = []
    try:
        for name in sorted(os.listdir(target), key=str.lower):
            if name.startswith(".") and name != ".trash":
                continue
            full = os.path.join(target, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append({
                "name": name,
                "type": "dir" if os.path.isdir(full) else "file",
                "size": st.st_size if os.path.isfile(full) else None,
                "modified_at": int(st.st_mtime),
            })
    except OSError as e:
        raise HTTPException(500, str(e))
    return {"path": path or "", "type": "dir", "entries": entries}


# Directories never worth syncing to the browser (heavy / regenerable / VCS internals).
_MANIFEST_SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".trash", ".next", ".nuxt",
    "dist", "build", "out", "target", "__pycache__", ".venv", "venv",
    ".cache", ".mypy_cache", ".pytest_cache", ".gradle", ".idea", ".turbo",
}
_MANIFEST_MAX_FILES = 20_000


@router.get("/workspaces/{wid}/manifest")
async def workspace_manifest(wid: str, _: Request, user=Depends(require_user)):
    """Recursive snapshot of every (syncable) file in the workspace: relative path,
    size, and mtime (unix seconds). The browser diffs this against the local folder
    to decide what to upload/download for two-way sync. Heavy/VCS dirs are skipped."""
    ws = db.get_workspace(wid, user["id"])
    if not ws:
        raise HTTPException(404, "not found")
    root = os.path.realpath(_workspace_dir(user["id"], ws["slug"]))
    if not os.path.isdir(root):
        _ensure_dir_user_writable(root)
    files = []
    truncated = False
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip-dirs in place so os.walk doesn't descend into them.
        dirnames[:] = [d for d in dirnames if d not in _MANIFEST_SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            files.append({"path": rel, "size": st.st_size, "modified_at": int(st.st_mtime)})
            if len(files) >= _MANIFEST_MAX_FILES:
                truncated = True
                break
        if truncated:
            break
    return {"files": files, "truncated": truncated}


@router.post("/workspaces/{wid}/upload")
async def upload_to_workspace(wid: str, _: Request,
                               file: UploadFile = File(...),
                               rel_path: str = Form("", description="Relative target including filename"),
                               user=Depends(require_user)):
    ws = db.get_workspace(wid, user["id"])
    if not ws:
        raise HTTPException(404, "not found")
    # Use rel_path if given (for webkitdirectory uploads preserving subdirs), else just the bare filename.
    target_rel = (rel_path or "").strip() or (file.filename or "upload.bin")
    if not target_rel:
        raise HTTPException(400, "no destination")
    abs_target = _resolve(user["id"], ws, target_rel)
    os.makedirs(os.path.dirname(abs_target) or os.path.dirname(_workspace_dir(user["id"], ws["slug"])),
                exist_ok=True)
    written = 0
    with open(abs_target, "wb") as out:
        while True:
            chunk = await file.read(1 << 20)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                try: os.remove(abs_target)
                except OSError: pass
                raise HTTPException(413, "file too large")
            out.write(chunk)
    try:
        os.chown(abs_target, 1000, 1000)
    except (OSError, PermissionError):
        pass
    return {"path": target_rel, "size": written}


@router.get("/workspaces/{wid}/download")
async def download_from_workspace(wid: str, _: Request,
                                   path: str = Query(..., description="Relative file path"),
                                   inline: int = 0,
                                   user=Depends(require_user)):
    ws = db.get_workspace(wid, user["id"])
    if not ws:
        raise HTTPException(404, "not found")
    target = _resolve(user["id"], ws, path)
    if not os.path.isfile(target):
        raise HTTPException(404, "not found")
    mime, _ = mimetypes.guess_type(target)
    mime = mime or "application/octet-stream"
    fname = os.path.basename(target)
    if inline:
        if mime in preview.PREVIEW_FORMATS:
            pdf_path = await preview.convert_to_pdf(target)
            if pdf_path:
                return FileResponse(
                    pdf_path, media_type="application/pdf",
                    filename=f"{fname}.pdf", content_disposition_type="inline",
                )
            raise HTTPException(500, "preview conversion failed")
        if (mime in preview.INLINE_NATIVE
                or mime.startswith("image/")
                or preview.is_text_like(mime, fname)):
            return FileResponse(
                target, media_type=mime,
                filename=fname, content_disposition_type="inline",
            )
    return FileResponse(target, media_type=mime, filename=fname)


@router.delete("/workspaces/{wid}/files")
async def delete_workspace_file(wid: str, _: Request,
                                 path: str = Query(...),
                                 user=Depends(require_user)):
    ws = db.get_workspace(wid, user["id"])
    if not ws:
        raise HTTPException(404, "not found")
    target = _resolve(user["id"], ws, path)
    root = _workspace_dir(user["id"], ws["slug"])
    if os.path.realpath(target) == os.path.realpath(root):
        raise HTTPException(400, "cannot delete workspace root")
    if not os.path.exists(target):
        raise HTTPException(404, "not found")
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    else:
        try: os.remove(target)
        except OSError as e: raise HTTPException(500, str(e))
    return {"ok": True}


@router.delete("/workspaces/{wid}")
async def delete_user_workspace(wid: str, _: Request, user=Depends(require_user)):
    rec = db.get_workspace(wid, user["id"])
    if not rec:
        raise HTTPException(404, "not found")
    db.delete_workspace(wid, user["id"])
    path = _workspace_dir(user["id"], rec["slug"])
    if os.path.isdir(path):
        # Move to a trash sibling to make accidents recoverable from the host filesystem.
        try:
            trash_root = os.path.join(config.WORKSPACES_DIR, user["id"], ".trash")
            os.makedirs(trash_root, exist_ok=True)
            shutil.move(path, os.path.join(trash_root, f"{rec['slug']}-{rec['id'][:8]}"))
        except OSError:
            pass
    return {"ok": True}
