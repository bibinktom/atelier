"""On-demand conversion of Office documents (xlsx/pptx/docx + their legacy
and ODF cousins) to PDF, so the frontend can render them inline.

Why this is a thin client: LibreOffice has a long history of memory-corruption
CVEs in document parsers. Running it inside the backend would expose all the
backend's secrets (NIM/Google/Tavily/Session keys), the SQLite DB, and the
read-write workspaces bind mount to a single parser bug. Instead we delegate
to a sandboxed sidecar (`preview` service: cap_drop:ALL, no-new-privileges,
non-root, read-only rootfs, /files + /workspaces mounted READ-ONLY).

This module keeps:
  - the cache (under `/tmp/preview-cache/` in the backend container, since the
    backend is the one serving the eventual PDF response)
  - per-key asyncio locks so concurrent requests on the same source don't both
    round-trip through the sidecar
  - format constants used by the routes to decide whether a request needs
    conversion or can be served inline directly.

Cache wipes on container restart; first hit re-converts in 1-3 s.
"""
import asyncio
import hashlib
import os
from pathlib import Path

import httpx

PREVIEW_URL = os.environ.get("PREVIEW_URL", "http://preview:8002")

# MIME types we'll convert to PDF for inline preview.
PREVIEW_FORMATS = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",        # .xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation", # .pptx
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/msword",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
    "application/vnd.oasis.opendocument.text",
    "text/csv",
}

# MIME types the browser can render natively without conversion.
INLINE_NATIVE = {
    "application/pdf",
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
}

CACHE_DIR = Path("/tmp/preview-cache")

_locks: dict[str, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _get_lock(key: str) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        lock = _locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _locks[key] = lock
        return lock


def _cache_key(src: Path) -> str:
    st = src.stat()
    raw = f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def convert_to_pdf(src_path: str, *, timeout: float = 60.0) -> str | None:
    """Convert an Office document at `src_path` to PDF via the preview sidecar.

    Returns the absolute path to the cached PDF on success, or None if the
    source is missing / conversion fails / the timeout fires.

    Caller must already have validated `src_path` (i.e. it must be a path the
    caller is authorised to read). The sidecar re-validates as defense in
    depth: it only accepts paths under /files or /workspaces with allowlisted
    extensions.
    """
    src = Path(src_path)
    if not src.is_file():
        return None
    try:
        key = _cache_key(src)
    except OSError:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{key}.pdf"
    if out.is_file() and out.stat().st_size > 0:
        return str(out)

    lock = await _get_lock(key)
    async with lock:
        # Re-check after acquiring the lock — another coroutine may have produced it.
        if out.is_file() and out.stat().st_size > 0:
            return str(out)

        try:
            async with httpx.AsyncClient(timeout=timeout + 10.0) as client:
                r = await client.post(
                    f"{PREVIEW_URL}/convert",
                    json={"path": str(src.resolve()), "timeout": timeout},
                )
        except httpx.RequestError as e:
            print(f"[preview] sidecar transport: {type(e).__name__}: {e}")
            return None
        if r.status_code != 200:
            body = r.text[:300] if r.headers.get("content-type", "").startswith("application/json") \
                   or r.headers.get("content-type", "").startswith("text/") else "<binary>"
            print(f"[preview] sidecar {r.status_code}: {body}")
            return None
        if not r.content:
            print("[preview] sidecar returned empty body")
            return None

        # Atomic rename: write to .tmp then rename, so a crashed write doesn't
        # leave a half-PDF that the next request happily serves.
        tmp = out.with_suffix(".pdf.tmp")
        tmp.write_bytes(r.content)
        os.replace(tmp, out)
        return str(out)


def is_text_like(mime: str | None, filename: str | None) -> bool:
    if mime and mime.startswith("text/"):
        return True
    if mime in INLINE_NATIVE:
        return True
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in {"md", "markdown", "txt", "log", "json", "yaml", "yml",
                   "py", "js", "ts", "tsx", "jsx", "html", "css", "sh",
                   "toml", "ini", "cfg", "csv", "tsv", "xml", "rst"}:
            return True
    return False
