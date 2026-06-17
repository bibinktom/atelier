"""Client for the `guard` AI-firewall sidecar (LLM Guard scanners).

Mirrors preview.py: a thin async HTTP client to a hardened sidecar on the
internal network. The sidecar does the model inference; this module just shapes
the verdict and decides fail-open vs fail-closed behaviour.

Posture (hybrid, per config):
  - input  → BLOCK when the prompt-injection / toxicity scanners reject it.
  - output → REDACT secrets in place (the sanitized text is delivered); toxicity
             is reported in `flagged` but not hard-blocked here.

Availability: if the sidecar is unreachable / errors, we FAIL OPEN by default
(`FIREWALL_FAIL_OPEN=1`) — chat stays up, but the failure is logged loudly and
recorded on a telemetry span. Set fail-open off to hard-gate on the firewall.
"""
import contextlib
import contextvars
import os
from dataclasses import dataclass, field

import httpx

from . import config

GUARD_URL = os.environ.get("GUARD_URL", "http://guard:8003")
_TIMEOUT = float(os.environ.get("FIREWALL_TIMEOUT", "4.0"))

# Per-turn policy overrides (resolved from the firewall_policy table by chat.py and
# pushed via using_policy()). A dict of {flag: 0|1|None}; None / missing means
# "inherit the global config default". Threaded through a ContextVar so the scan
# functions don't need a policy argument plumbed through every call site —
# mirroring nim.using_creds().
_GLOBAL_DEFAULTS = {
    "fail_open": None,        # resolved against config.FIREWALL_FAIL_OPEN
    "tool_scan": None,        # config.FIREWALL_TOOL_SCAN
    "code_scan": None,        # config.FIREWALL_CODE_SCAN
    "pii_output": None,       # config.FIREWALL_PII_OUTPUT (passed to the sidecar)
    "buffer_output": None,    # config.FIREWALL_BUFFER_OUTPUT (read by chat.py)
    "alignment_check": None,  # config.FIREWALL_ALIGNMENT_CHECK (read by chat.py)
    "alignment_block": None,  # config.FIREWALL_ALIGNMENT_BLOCK (read by chat.py)
}
_CONFIG_FOR = {
    "fail_open": "FIREWALL_FAIL_OPEN",
    "tool_scan": "FIREWALL_TOOL_SCAN",
    "code_scan": "FIREWALL_CODE_SCAN",
    "pii_output": "FIREWALL_PII_OUTPUT",
    "buffer_output": "FIREWALL_BUFFER_OUTPUT",
    "alignment_check": "FIREWALL_ALIGNMENT_CHECK",
    "alignment_block": "FIREWALL_ALIGNMENT_BLOCK",
}
_policy_var: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "firewall_policy", default=None)


def flag(name: str) -> bool:
    """Resolve a firewall flag for the current turn: per-user override if set,
    else the global config default."""
    pol = _policy_var.get()
    if pol is not None and pol.get(name) is not None:
        return bool(pol[name])
    return bool(getattr(config, _CONFIG_FOR[name]))


@contextlib.contextmanager
def using_policy(policy: dict | None):
    """Bind a per-user policy dict for the duration of a turn."""
    token = _policy_var.set(policy)
    try:
        yield
    finally:
        _policy_var.reset(token)


@dataclass
class Verdict:
    blocked: bool = False              # input: refuse turn? / tool: injection detected?
    sanitized: str = ""                # possibly-redacted text
    flagged: list[str] = field(default_factory=list)
    scores: dict = field(default_factory=dict)
    changed: bool = False              # output: did redaction alter the text?
    error: str | None = None           # set when the sidecar failed (fail-open)


@dataclass
class CodeVerdict:
    insecure: bool = False
    treatment: str | None = None       # CodeShield "block" / "warn"
    issues: list = field(default_factory=list)  # [{pattern_id, description, severity, line}]
    error: str | None = None


async def _call(path: str, payload: dict) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            r = await client.post(f"{GUARD_URL}{path}", json=payload)
        if r.status_code != 200:
            print(f"[firewall] sidecar {path} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except httpx.RequestError as e:
        print(f"[firewall] sidecar transport on {path}: {type(e).__name__}: {e}")
        return None


def _fail_open(original: str, what: str) -> Verdict:
    """Sidecar unreachable. Don't block chat (unless configured to); log loudly."""
    fail_open = flag("fail_open")
    print(f"[firewall] sidecar unavailable during {what} scan — "
          f"{'failing open' if fail_open else 'failing closed'}")
    if fail_open:
        return Verdict(blocked=False, sanitized=original, error="sidecar_unavailable")
    return Verdict(blocked=True, sanitized=original, flagged=["firewall_unavailable"],
                   error="sidecar_unavailable")


async def scan_input(text: str) -> Verdict:
    if not config.FIREWALL_ENABLED or not (text or "").strip():
        return Verdict(sanitized=text or "")
    data = await _call("/scan/input", {"text": text})
    if data is None:
        return _fail_open(text, "input")
    return Verdict(
        blocked=not data.get("valid", True),
        sanitized=data.get("sanitized", text),
        flagged=data.get("flagged", []),
        scores=data.get("scores", {}),
    )


async def scan_output(prompt: str, text: str) -> Verdict:
    if not config.FIREWALL_ENABLED or not (text or "").strip():
        return Verdict(sanitized=text or "")
    # Pass the per-user PII decision to the sidecar (it builds the Sensitive scanner
    # in/out accordingly); secret redaction always runs regardless.
    data = await _call("/scan/output",
                       {"prompt": prompt or "", "text": text, "pii": flag("pii_output")})
    if data is None:
        return _fail_open(text, "output")
    sanitized = data.get("sanitized", text)
    return Verdict(
        blocked=False,                       # output is redacted, never hard-blocked here
        sanitized=sanitized,
        flagged=data.get("flagged", []),
        scores=data.get("scores", {}),
        changed=(sanitized != text),
    )


async def scan_tool_result(text: str) -> Verdict:
    """Scan untrusted tool-result text for indirect prompt injection. `blocked`
    here means 'injection detected' — the caller defangs (warns the model), it
    does not abort the turn."""
    if (not config.FIREWALL_ENABLED or not flag("tool_scan")
            or not (text or "").strip()):
        return Verdict(sanitized=text or "")
    data = await _call("/scan/tool", {"text": text})
    if data is None:
        # Fail open: don't flag what we couldn't scan.
        return Verdict(blocked=False, sanitized=text or "", error="sidecar_unavailable")
    return Verdict(
        blocked=bool(data.get("injection")),
        sanitized=text or "",
        flagged=data.get("flagged", []),
        scores=data.get("scores", {}),
    )


async def scan_code(code: str) -> CodeVerdict:
    """Scan LLM-written/cloned code for insecure patterns (CodeShield). Advisory —
    the caller warns the coder; it never blocks."""
    if (not config.FIREWALL_ENABLED or not flag("code_scan")
            or not (code or "").strip()):
        return CodeVerdict()
    data = await _call("/scan/code", {"code": code})
    if data is None:
        return CodeVerdict(error="sidecar_unavailable")  # fail open: insecure=False
    return CodeVerdict(
        insecure=bool(data.get("is_insecure")),
        treatment=data.get("treatment"),
        issues=data.get("issues", []),
    )
