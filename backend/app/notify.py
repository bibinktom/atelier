"""Push notifications via ntfy.

Single channel right now: a pending-approval prompt with inline Approve/Deny
buttons. The admin's phone gets the alert; tapping a button hits the backend
over the Cloudflare tunnel with a single-use token (no session cookie needed).

ntfy is best-effort: failures are logged and swallowed so the OAuth callback
never fails because the push didn't go through.
"""
import secrets
import urllib.request as urlreq

from . import config, db


def _ascii_safe(s: str) -> str:
    """HTTP/1.1 header values are encoded as latin-1; ntfy ignores headers it
    can't encode and the request fails outright. Strip everything outside
    ASCII so titles/tags survive em-dashes, smart quotes, etc."""
    return s.encode("ascii", "replace").decode("ascii").replace("?", "-")


def _push(title: str, body: str, *, tags: str = "rotating_light",
          priority: str = "default", actions: str | None = None) -> None:
    if not config.NTFY_TOPIC:
        return
    url = f"{config.NTFY_SERVER}/{config.NTFY_TOPIC}"
    headers: dict[str, str] = {
        "Title": _ascii_safe(title),
        "Priority": priority,
        "Tags": _ascii_safe(tags),
    }
    if actions:
        headers["Actions"] = _ascii_safe(actions)
    try:
        req = urlreq.Request(url, data=body.encode("utf-8"), headers=headers)
        urlreq.urlopen(req, timeout=4)
    except Exception as e:  # noqa: BLE001 — best-effort
        print(f"[notify] ntfy push failed: {e}", flush=True)


def push_pending_approval(user: dict) -> None:
    """Mint a per-user single-use token and push a notification with
    Approve/Deny action buttons that target the public backend URL."""
    if not config.NTFY_TOPIC:
        return
    base = (config.PUBLIC_BACKEND_URL or "").rstrip("/")
    if not base:
        print("[notify] PUBLIC_BACKEND_URL not set — skipping push", flush=True)
        return

    token = secrets.token_urlsafe(24)
    db.set_approval_token(user["id"], token)
    # ntfy's Actions-header parser mangles `?` and `=` in URLs (rewrites
    # `?token=XYZ` to `-token%3DXYZ`), so the token has to ride the path.
    approve_url = f"{base}/auth/approve_via_token/{token}"
    deny_url = f"{base}/auth/deny_via_token/{token}"

    name = (user.get("name") or "").strip()
    email = user.get("email", "")
    who = f"{name} ({email})" if name and name != email.split("@")[0] else email

    title = "Atelier - approval needed"
    body = f"{who} just signed in and is waiting for admin approval."
    actions = (
        f"http, Approve, {approve_url}, method=POST, clear=true; "
        f"http, Deny, {deny_url}, method=POST, clear=true"
    )
    _push(title, body, tags="bell", priority="high", actions=actions)


def push_action_confirmation(user_email: str, action: str) -> None:
    """Best-effort confirmation toast back to the admin phone after they
    tap Approve / Deny. Plain notification — no buttons."""
    title = f"Atelier - {user_email} {action}"
    body = f"Action accepted at {db.now()}."
    _push(title, body, tags="white_check_mark" if action == "approved" else "no_entry",
          priority="low")
