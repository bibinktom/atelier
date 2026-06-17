"""LibreOffice-in-a-sandbox conversion service.

Reachable only from the backend on the internal docker network. The container
is hardened (cap_drop:ALL, no-new-privileges, non-root) and mounts /files +
/workspaces READ-ONLY -- even an RCE here cannot tamper with the canonical
files. The converted PDF is returned in the HTTP response body, never written
to a shared volume, so the sidecar holds no state between calls.

Why a separate service: LibreOffice has a long history of memory-corruption
CVEs in document parsers. Putting it in the backend container would expose
SESSION_SECRET, NIM/Google/Tavily keys, the SQLite DB, and the workspaces bind
mount (rw) to a single parser bug. Here, all of those are unreachable.
"""
import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

# Allowlisted source roots. The backend already validates user input and
# resolves to an absolute path before sending; we re-validate here as
# defense in depth -- even if an attacker reaches this endpoint somehow,
# paths outside these roots are rejected.
ALLOWED_ROOTS = (
    Path("/files").resolve(),
    Path("/workspaces").resolve(),
)

ALLOWED_EXT = {".xlsx", ".pptx", ".docx", ".xls", ".ppt", ".doc",
               ".ods", ".odp", ".odt", ".csv"}

MAX_INPUT_BYTES = 50 * 1024 * 1024   # 50 MB. Same cap as workspace upload.

app = FastAPI(title="Atelier preview converter")


class ConvertBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)
    timeout: float = Field(60.0, ge=5.0, le=120.0)


def _is_allowed(p: Path) -> bool:
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    if rp.suffix.lower() not in ALLOWED_EXT:
        return False
    if not rp.is_file():
        return False
    try:
        if rp.stat().st_size > MAX_INPUT_BYTES:
            return False
    except OSError:
        return False
    for root in ALLOWED_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/convert")
async def convert(body: ConvertBody):
    src = Path(body.path)
    if not _is_allowed(src):
        raise HTTPException(400, "path not allowed")

    with tempfile.TemporaryDirectory(prefix="lo_", dir="/tmp") as tmp:
        tmpdir = Path(tmp)
        profile = tmpdir / "profile"
        # Argv-form spawn (no shell). Each call gets its own UserInstallation
        # so concurrent xlsx + pptx conversions don't fight one shared profile.
        cmd = [
            "libreoffice",
            "--headless",
            "--norestore",
            "--nologo",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to", "pdf",
            "--outdir", str(tmpdir),
            str(src),
        ]
        spawn = asyncio.create_subprocess_exec
        proc = await spawn(
            *cmd,
            env={"HOME": str(tmpdir), "PATH": "/usr/bin:/bin"},
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=body.timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise HTTPException(504, "conversion timed out")
        if proc.returncode != 0:
            msg = (err or b"").decode(errors="ignore")[:300]
            raise HTTPException(500, f"libreoffice exit {proc.returncode}: {msg}")
        produced = sorted(tmpdir.glob("*.pdf"))
        if not produced:
            raise HTTPException(500, "no PDF produced")
        pdf_bytes = produced[0].read_bytes()
        if not pdf_bytes:
            raise HTTPException(500, "empty PDF")
        return Response(content=pdf_bytes, media_type="application/pdf")
