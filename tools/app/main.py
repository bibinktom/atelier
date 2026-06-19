"""Sandboxed tool server. Reachable only by the backend on the internal docker network."""
import os
import uuid
from typing import Any

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from .pdf_gen import build_pdf
from .docx_gen import build_docx
from .xlsx_gen import build_xlsx
from .pptx_gen import build_pptx
from .flyer_gen import build_flyer
from .search import tavily_search
from .web_fetch import web_fetch as do_web_fetch
from . import workspace as ws

FILES_DIR = os.environ.get("FILES_DIR", "/files")
os.makedirs(FILES_DIR, exist_ok=True)
# The /workspaces root only exists in the shared-container build; the local desktop
# build (ATELIER_LOCAL=1) operates on absolute host paths and never uses it.
if not ws.LOCAL:
    os.makedirs("/workspaces", exist_ok=True)

app = FastAPI(title="Family AI Tools")

log = logging.getLogger("tools")
logging.basicConfig(level=logging.INFO)


@app.exception_handler(RequestValidationError)
async def _log_validation(request: Request, exc: RequestValidationError):
    # Log the body shape so we can see what the model actually sent when 422s happen.
    try:
        body = await request.json()
        snippet = str(body)[:600]
    except Exception:
        snippet = "<unreadable>"
    log.warning("422 on %s — errors=%s body=%s", request.url.path, exc.errors()[:3], snippet)
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------- search + fetch ----------

class SearchBody(BaseModel):
    query: str
    max_results: int = Field(5, ge=1, le=10)


@app.post("/web_search")
async def web_search(body: SearchBody):
    return await tavily_search(body.query, body.max_results)


class FetchBody(BaseModel):
    url: str
    max_chars: int = Field(20_000, ge=500, le=80_000)


@app.post("/web_fetch")
async def web_fetch_endpoint(body: FetchBody):
    return await do_web_fetch(body.url, body.max_chars)


# ---------- file generators ----------

def _alloc(suffix: str) -> tuple[str, str, str]:
    fid = uuid.uuid4().hex
    fname = f"{fid}{suffix}"
    path = os.path.join(FILES_DIR, fname)
    return fid, fname, path


def _safe_filename(name: str, fallback: str, ext: str) -> str:
    name = (name or fallback).strip().replace("/", "_").replace("\\", "_")
    if not name.lower().endswith(ext):
        name = f"{name}{ext}"
    return name[:120]


class PdfBody(BaseModel):
    filename: str
    title: str
    body_markdown: str


@app.post("/generate_pdf")
def generate_pdf(body: PdfBody):
    _, _, path = _alloc(".pdf")
    try:
        build_pdf(path, body.title, body.body_markdown)
    except Exception as e:
        raise HTTPException(500, f"pdf build failed: {e}")
    fname = _safe_filename(body.filename, "document", ".pdf")
    return {"file": {"filename": fname, "path": path,
                     "mime": "application/pdf", "size": os.path.getsize(path)}}


class DocxBody(BaseModel):
    filename: str
    title: str
    body_markdown: str


@app.post("/generate_docx")
def generate_docx(body: DocxBody):
    _, _, path = _alloc(".docx")
    try:
        build_docx(path, body.title, body.body_markdown)
    except Exception as e:
        raise HTTPException(500, f"docx build failed: {e}")
    fname = _safe_filename(body.filename, "document", ".docx")
    return {"file": {"filename": fname, "path": path,
                     "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     "size": os.path.getsize(path)}}


class SheetSpec(BaseModel):
    name: str
    rows: list[list[Any]]

    @field_validator("rows", mode="before")
    @classmethod
    def _normalize_rows(cls, v: Any) -> Any:
        # Models often send rows as a list of dicts ({"col": val, ...}) instead of a list of arrays.
        # Convert that shape to header + data rows so the call doesn't fail.
        if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
            keys: list[Any] = []
            seen: set[Any] = set()
            for r in v:
                for k in r.keys():
                    if k not in seen:
                        keys.append(k); seen.add(k)
            data = [[r.get(k) for k in keys] for r in v]
            return [list(keys), *data]
        # Wrap a single string row in a list to be forgiving.
        if isinstance(v, list):
            return [list(r) if isinstance(r, tuple) else r for r in v]
        return v


class XlsxBody(BaseModel):
    filename: str
    sheets: list[SheetSpec]


@app.post("/generate_xlsx")
def generate_xlsx(body: XlsxBody):
    _, _, path = _alloc(".xlsx")
    try:
        build_xlsx(path, [s.model_dump() for s in body.sheets])
    except Exception as e:
        raise HTTPException(500, f"xlsx build failed: {e}")
    fname = _safe_filename(body.filename, "workbook", ".xlsx")
    return {"file": {"filename": fname, "path": path,
                     "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     "size": os.path.getsize(path)}}


class SlideSpec(BaseModel):
    title: str
    bullets: list[str] = []
    notes: str | None = None


class PptxBody(BaseModel):
    filename: str
    title: str
    subtitle: str | None = None
    slides: list[SlideSpec]


class FlyerBody(BaseModel):
    filename: str
    title: str
    subtitle: str | None = None
    features: list[str] = []
    footer: str | None = None
    cta_text: str | None = None
    accent_color: str | None = "#E63946"
    background_color: str | None = "#FFFFFF"
    text_color: str | None = "#1A1A1A"
    hero_image_path: str | None = None    # absolute server path under /files (e.g. user upload)


@app.post("/generate_flyer")
def generate_flyer(body: FlyerBody):
    _, _, path = _alloc(".pdf")
    # Path safety for hero image: must live under FILES_DIR
    hero = body.hero_image_path or None
    if hero:
        real = os.path.realpath(hero)
        files_root = os.path.realpath(FILES_DIR)
        if not (real == files_root or real.startswith(files_root + os.sep)) or not os.path.isfile(real):
            hero = None
    try:
        build_flyer(
            path,
            title=body.title,
            subtitle=body.subtitle or "",
            features=body.features or [],
            footer=body.footer or "",
            cta_text=body.cta_text or "",
            accent_color=body.accent_color or "#E63946",
            background_color=body.background_color or "#FFFFFF",
            text_color=body.text_color or "#1A1A1A",
            hero_image_path=hero,
        )
    except Exception as e:
        raise HTTPException(500, f"flyer build failed: {e}")
    fname = _safe_filename(body.filename, "flyer", ".pdf")
    return {"file": {"filename": fname, "path": path,
                     "mime": "application/pdf", "size": os.path.getsize(path)}}


@app.post("/generate_pptx")
def generate_pptx(body: PptxBody):
    _, _, path = _alloc(".pptx")
    try:
        build_pptx(path, body.title, body.subtitle or "", [s.model_dump() for s in body.slides])
    except Exception as e:
        raise HTTPException(500, f"pptx build failed: {e}")
    fname = _safe_filename(body.filename, "deck", ".pptx")
    return {"file": {"filename": fname, "path": path,
                     "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                     "size": os.path.getsize(path)}}


# ---------- workspace (per-conversation sandboxed scratch dir) ----------
# Backend injects workspace_path = "<user_id>/<workspace_slug>".  The LLM never sees it.

class WSList(BaseModel):
    workspace_path: str
    path: str = "."


@app.post("/workspace_list")
def workspace_list(body: WSList):
    try:
        return ws.list_dir(body.workspace_path, body.path)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSRead(BaseModel):
    workspace_path: str
    path: str
    max_chars: int = Field(50_000, ge=500, le=200_000)


@app.post("/workspace_read")
def workspace_read(body: WSRead):
    try:
        return ws.read_file(body.workspace_path, body.path, body.max_chars)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSWrite(BaseModel):
    workspace_path: str
    path: str
    content: str


@app.post("/workspace_write")
def workspace_write(body: WSWrite):
    try:
        return ws.write_file(body.workspace_path, body.path, body.content)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSEdit(BaseModel):
    workspace_path: str
    path: str
    old: str
    new: str


@app.post("/workspace_edit")
def workspace_edit(body: WSEdit):
    try:
        return ws.edit_file(body.workspace_path, body.path, body.old, body.new)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSGrep(BaseModel):
    workspace_path: str
    pattern: str
    path: str = "."


@app.post("/workspace_grep")
def workspace_grep(body: WSGrep):
    try:
        return ws.grep(body.workspace_path, body.pattern, body.path)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSGlob(BaseModel):
    workspace_path: str
    pattern: str


@app.post("/workspace_glob")
def workspace_glob(body: WSGlob):
    try:
        return ws.glob_files(body.workspace_path, body.pattern)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSBash(BaseModel):
    workspace_path: str
    command: str
    timeout: int = 30


@app.post("/workspace_bash")
def workspace_bash(body: WSBash):
    try:
        return ws.bash(body.workspace_path, body.command, body.timeout)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSGitClone(BaseModel):
    workspace_path: str
    url: str
    subdir: str = ""


@app.post("/workspace_git_clone")
def workspace_git_clone(body: WSGitClone):
    try:
        return ws.git_clone(body.workspace_path, body.url, body.subdir)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSApplyPatch(BaseModel):
    workspace_path: str
    patch: str


@app.post("/workspace_apply_patch")
def workspace_apply_patch(body: WSApplyPatch):
    try:
        return ws.apply_patch(body.workspace_path, body.patch)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


class WSCodebaseSearch(BaseModel):
    workspace_path: str
    query: str
    max_results: int = 12


@app.post("/codebase_search")
def codebase_search(body: WSCodebaseSearch):
    try:
        return ws.codebase_search(body.workspace_path, body.query, body.max_results)
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))


# ---- device / capability tools (local desktop build only) ----
# These talk to USB hardware and provision external CLIs on the user's own machine,
# so they're disabled in the shared-container server build.

def _require_local():
    if not ws.LOCAL:
        raise HTTPException(400, "device tools are only available in the local desktop app")


class EnsureCapBody(BaseModel):
    name: str


@app.post("/ensure_capability")
def ensure_capability(body: EnsureCapBody):
    """Make an external tool available (installing it on demand): adb, arduino-cli,
    esptool, mpremote, ampy, or detect a system tool (ssh, git, …)."""
    _require_local()
    from . import capabilities
    return capabilities.ensure(body.name)


@app.post("/list_capabilities")
def list_capabilities(_: dict | None = None):
    """List known device/connectivity capabilities and whether each is installed."""
    _require_local()
    from . import capabilities
    return capabilities.catalog()


# USB-serial bridge chips common on ESP32 / Arduino boards → a friendlier hint.
_BOARD_HINTS = {0x10c4: "Silicon Labs CP210x (ESP32 dev board)", 0x1a86: "WCH CH340 (ESP/Arduino clone)",
                0x0403: "FTDI (Arduino/serial)", 0x2341: "Arduino", 0x239a: "Adafruit",
                0x303a: "Espressif (native USB)"}


class SerialListBody(BaseModel):
    pass


@app.post("/serial_list")
def serial_list(_: SerialListBody | None = None):
    """List serial ports (USB-connected ESP32 / Arduino / microcontrollers)."""
    _require_local()
    try:
        from serial.tools import list_ports
    except ImportError:
        return {"error": "pyserial not available", "ports": []}
    ports = []
    for p in list_ports.comports():
        ports.append({
            "device": p.device,
            "description": p.description,
            "hwid": p.hwid,
            "vid": p.vid, "pid": p.pid,
            "manufacturer": p.manufacturer,
            "serial_number": p.serial_number,
            "likely_board": _BOARD_HINTS.get(p.vid) if p.vid else None,
        })
    return {"ports": ports, "count": len(ports)}
