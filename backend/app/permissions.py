"""Action-permission gate for the local desktop build.

The agent runs on the user's own machine with their real shell, so before a
genuinely destructive or device-writing command we pause and ask the user to
Allow / Deny / Always-allow — Claude-Code style. Only meaningful locally
(ATELIER_LOCAL); the shared server build relies on its container sandbox instead.

Classification is deliberately narrow: ordinary file edits, reads, builds, package
installs and web tools never prompt (that would be prompt fatigue). We gate the
small set of operations that can wipe data, reflash hardware, or escalate.
"""
import asyncio
import re

from . import config, db


def enabled() -> bool:
    return config.ATELIER_LOCAL and config.PERMISSIONS_ENABLED


# (pattern, human reason, severity, rule category). The category is the "always
# allow" key, so approving once whitelists that whole class for the user.
_BASH_RULES: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r"\brm\s+(?:-\S*\s+)*-\S*[rf]", re.I), "delete files recursively/forcibly (rm)", "high", "rm"),
    (re.compile(r"\b(?:mkfs|fdisk|parted)\b", re.I), "format or repartition a disk", "high", "disk"),
    (re.compile(r"\bdd\b\s+(?:if|of)=", re.I), "raw disk write (dd)", "high", "disk"),
    (re.compile(r">\s*/dev/(?:sd|nvme|disk|mmcblk)", re.I), "write directly to a disk device", "high", "disk"),
    (re.compile(r"\b(?:shutdown|reboot|halt|poweroff)\b", re.I), "power off / reboot the computer", "high", "power"),
    (re.compile(r"(?:^|\s)sudo\s", re.I), "run as administrator (sudo)", "high", "sudo"),
    (re.compile(r":\(\)\s*\{\s*:\|:", re.I), "fork bomb", "high", "forkbomb"),
    (re.compile(r"\bgit\s+push\b.*(?:--force\b|-f\b|\+)", re.I), "force-push (overwrites remote history)", "high", "git-force"),
    (re.compile(r"\b(?:chmod|chown)\s+-R\b", re.I), "recursively change permissions/ownership", "medium", "perms"),
    (re.compile(r"\besptool(?:\.py)?\b.*\b(?:write_flash|erase_flash|erase_region)\b", re.I), "flash / erase an ESP chip", "high", "flash"),
    (re.compile(r"\barduino-cli\s+(?:upload|burn-bootloader)\b", re.I), "upload firmware to a board", "high", "flash"),
    (re.compile(r"\badb\s+(?:install|uninstall|push|root|reboot|shell\s+rm|shell\s+pm)\b", re.I), "modify the connected phone (adb)", "medium", "adb-write"),
    (re.compile(r"\bcurl\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I), "run a script downloaded from the internet", "high", "curl-pipe-sh"),
    (re.compile(r"\bwget\b[^|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I), "run a script downloaded from the internet", "high", "curl-pipe-sh"),
]


def classify(name: str, args: dict) -> dict:
    """Decide whether a tool call needs the user's confirmation.
    Returns {needs, reason, severity, rule_key, command}."""
    if name == "workspace_bash":
        cmd = str((args or {}).get("command") or "")
        for pat, reason, sev, cat in _BASH_RULES:
            if pat.search(cmd):
                return {"needs": True, "reason": reason, "severity": sev,
                        "rule_key": f"bash:{cat}", "command": cmd[:400]}
    return {"needs": False}


# ---- pending interactive decisions (in-memory, keyed by request id) ----

_pending: dict[str, asyncio.Future] = {}


def new_request(request_id: str) -> asyncio.Future:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending[request_id] = fut
    return fut


def resolve(request_id: str, decision: str) -> bool:
    """Called by the HTTP route when the user clicks Allow/Deny/Always. Returns
    True if a pending request was waiting."""
    fut = _pending.pop(request_id, None)
    if fut is not None and not fut.done():
        fut.set_result(decision)
        return True
    return False


def cancel(request_id: str) -> None:
    _pending.pop(request_id, None)
