"""AI firewall sidecar — LLM Guard scanners behind FastAPI.

Reachable only by the backend on the internal docker network. Holds no secrets
and mounts no volumes: it only ever sees plain text over HTTP, making it the
lowest-privilege service in the stack. Endpoints are sync `def` so FastAPI runs
the CPU-bound scan in a worker thread instead of blocking the event loop.
"""
import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel
from llm_guard import scan_output, scan_prompt

from . import scanners

# CodeShield (Meta) — local Semgrep+regex insecure-code detector. Imported lazily/
# guarded so a packaging issue degrades to "code scan unavailable" instead of
# crashing the whole firewall.
try:
    from codeshield.cs import CodeShield
    _CODESHIELD_OK = True
except Exception as e:  # noqa: BLE001
    CodeShield = None
    _CODESHIELD_OK = False
    print(f"[guard] CodeShield unavailable: {type(e).__name__}: {e}")

# Cheap pre-model length cap (also bounds scan latency). Replaces the tiktoken-based
# TokenLimit scanner so the container needs no tiktoken download / network.
MAX_INPUT_CHARS = int(os.environ.get("FIREWALL_MAX_INPUT_CHARS", "24000"))

app = FastAPI(title="Atelier AI Firewall")
log = logging.getLogger("guard")
logging.basicConfig(level=logging.INFO)


class InputBody(BaseModel):
    text: str


class OutputBody(BaseModel):
    prompt: str = ""
    text: str
    pii: bool | None = None   # per-user override; None = use the sidecar's default


class ToolBody(BaseModel):
    text: str


class CodeBody(BaseModel):
    code: str
    language: str | None = None


def _flagged(valid: dict) -> list[str]:
    return [name for name, ok in valid.items() if not ok]


@app.get("/health")
def health():
    return {"ok": True, "codeshield": _CODESHIELD_OK}


@app.post("/scan/input")
def scan_input(body: InputBody):
    text = body.text or ""
    if len(text) > MAX_INPUT_CHARS:
        return {
            "sanitized": text[:MAX_INPUT_CHARS], "valid": False,
            "flagged": ["token_limit"], "scores": {"token_limit": 1.0},
        }
    sanitized, valid, scores = scan_prompt(scanners.input_scanners(), text)
    return {
        "sanitized": sanitized, "valid": all(valid.values()),
        "flagged": _flagged(valid), "scores": scores,
    }


@app.post("/scan/output")
def scan_output_ep(body: OutputBody):
    # Redact secrets ourselves (global re.sub), then run the ML output scanners
    # (toxicity) on the already-scrubbed text for flagging.
    redacted, n_secrets = scanners.redact_secrets(body.text or "")
    sanitized, valid, scores = scan_output(
        scanners.output_scanners(pii=body.pii), body.prompt or "", redacted,
    )
    sanitized, n_ssn = scanners.redact_ssn(sanitized, pii=body.pii)
    flagged = _flagged(valid)
    if n_secrets:
        flagged = ["Secrets", *flagged]
    if n_ssn and "Sensitive" not in flagged:
        flagged.append("Sensitive")
    return {
        "sanitized": sanitized, "valid": (n_secrets == 0) and (n_ssn == 0) and all(valid.values()),
        "flagged": flagged, "scores": scores,
    }


@app.post("/scan/tool")
def scan_tool(body: ToolBody):
    """Scan untrusted tool-result text for indirect prompt injection. Returns a
    detection only — the backend defangs (warns the model) rather than blocking."""
    text = (body.text or "")[:MAX_INPUT_CHARS]
    _, valid, scores = scan_prompt(scanners.tool_scanners(), text)
    return {
        "injection": not all(valid.values()),
        "scores": scores,
        "flagged": _flagged(valid),
    }


@app.post("/scan/code")
async def scan_code(body: CodeBody):
    """Scan LLM-written/cloned code for insecure patterns via CodeShield
    (local Semgrep+regex). Returns a finding only — the backend warns the coder."""
    if not _CODESHIELD_OK or not (body.code or "").strip():
        return {"available": _CODESHIELD_OK, "is_insecure": False,
                "treatment": None, "issues": []}
    result = await CodeShield.scan_code(body.code)
    issues = []
    for it in (getattr(result, "issues_found", None) or []):
        issues.append({
            "pattern_id": getattr(it, "pattern_id", None),
            "description": getattr(it, "description", None),
            "severity": str(getattr(it, "severity", "") or ""),
            "line": getattr(it, "line", None),
        })
    treatment = getattr(result, "recommended_treatment", None)
    return {
        "available": True,
        "is_insecure": bool(getattr(result, "is_insecure", False)),
        "treatment": str(treatment) if treatment is not None else None,
        "issues": issues,
    }
