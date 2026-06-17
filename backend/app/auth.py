import base64
import hashlib
import secrets

import httpx
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse

from . import config, crypto, db, firewall, notify

oauth = OAuth()
oauth.register(
    name="google",
    client_id=config.GOOGLE_CLIENT_ID,
    client_secret=config.GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

router = APIRouter(prefix="/auth", tags=["auth"])


# Hard cap on the pending-approvals queue. Prevents an OAuth-flood from filling the
# DB with junk accounts. Tunable via env without a code change.
_MAX_PENDING = int(__import__("os").environ.get("MAX_PENDING_USERS", "50"))


@router.get("/google/login")
async def google_login(request: Request):
    redirect_uri = f"{config.PUBLIC_BACKEND_URL}/auth/google/callback"
    # `prompt=select_account` forces Google to show the account picker even if
    # the user is already signed in to a Google account in this browser. Without
    # it, Google silently re-authenticates the active session — so a user who
    # just clicked "Sign out" in our UI would be bounced straight back into the
    # same account, with no way to switch.
    return await oauth.google.authorize_redirect(request, redirect_uri,
                                                  prompt="select_account")


@router.get("/google/callback")
async def google_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/login?error=oauth")
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/login?error=unverified")

    is_admin = (email == config.ADMIN_EMAIL)
    pre_approved = (
        is_admin
        or config.OPEN_SIGNUP                                    # self-serve: admit everyone
        or (config.ALLOWED_EMAILS and email in config.ALLOWED_EMAILS)
        or db.is_email_approved(email)
    )

    # Existing user? Their is_pending state is preserved.
    existing = None
    with db.connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            existing = dict(row)

    if existing is None and not pre_approved:
        # New external sign-up. Refuse if the queue is already overflowing —
        # protects against a script firing 1000 OAuth flows.
        if db.count_pending_users() >= _MAX_PENDING:
            return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/login?error=registration_full")

    is_pending = False
    if existing is None:
        is_pending = not pre_approved

    user = db.upsert_user(
        email=email,
        name=info.get("name") or email.split("@")[0],
        picture=info.get("picture") or "",
        is_admin=is_admin,
        is_pending=is_pending,
    )
    # Notify admin's phone whenever a freshly-created pending user shows up.
    # Existing pending users (logging in again before being approved) do not
    # trigger another push to avoid re-pinging the admin on every refresh.
    if existing is None and user.get("is_pending"):
        try:
            notify.push_pending_approval(user)
        except Exception as e:  # noqa: BLE001 — never block login
            print(f"[auth] approval push failed: {e}", flush=True)
    request.session["user_id"] = user["id"]
    return RedirectResponse(config.PUBLIC_FRONTEND_URL)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return JSONResponse({"ok": True})


@router.get("/me")
async def me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(401, "not authenticated")
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "picture": user["picture"],
        "is_admin": bool(user["is_admin"]),
        "is_pending": bool(user.get("is_pending")),
        # True once the user has connected their OpenRouter account. The encrypted
        # key itself is never returned — only whether one exists.
        "openrouter_connected": bool(user.get("openrouter_key_enc")),
    }


def current_user(request: Request) -> dict | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.get_user(uid)


def require_user(request: Request) -> dict:
    """Authenticated session, regardless of pending state. Used for /auth/me and
    pending-self-service endpoints (e.g. logout)."""
    user = current_user(request)
    if not user:
        raise HTTPException(401, "not authenticated")
    return user


def require_approved_user(request: Request) -> dict:
    """Gates every feature-bearing route — pending users can't chat, upload, run
    tools, see workspaces, etc. They get the waiting-room UI on the frontend."""
    user = require_user(request)
    if user.get("is_pending"):
        raise HTTPException(403, "account pending admin approval")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user.get("is_admin"):
        raise HTTPException(403, "admin only")
    return user


# ---------- OpenRouter OAuth (PKCE) ----------
#
# One-button "connect your OpenRouter account": the user authorizes our app on
# openrouter.ai, we exchange the returned code for a USER-SCOPED API key, encrypt
# it, and use it for that user's inference (so inference is user-funded). This is
# OpenRouter's bespoke PKCE flow — a redirect to /auth plus a key-exchange POST —
# not standard OIDC, so it's implemented manually rather than via Authlib.
# Defined here (after the require_* helpers) because the route Depends on require_user.

def _pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) with S256. Verifier is high-entropy; the
    challenge is base64url(sha256(verifier)) with padding stripped."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


@router.get("/openrouter/connect")
async def openrouter_connect(request: Request, user=Depends(require_user)):
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    # Stash in the signed, httponly session cookie. Single-use (popped on callback).
    request.session["or_verifier"] = verifier
    request.session["or_state"] = state
    callback = f"{config.PUBLIC_BACKEND_URL}/auth/openrouter/callback"
    url = (
        f"{config.OPENROUTER_OAUTH_URL}?callback_url={callback}"
        f"&code_challenge={challenge}&code_challenge_method=S256&state={state}"
    )
    return RedirectResponse(url)


@router.get("/openrouter/callback")
async def openrouter_callback(request: Request, code: str = "", state: str = "",
                              user=Depends(require_user)):
    fe = config.PUBLIC_FRONTEND_URL
    expected_state = request.session.pop("or_state", None)
    verifier = request.session.pop("or_verifier", None)
    # State nonce defeats callback CSRF (an attacker injecting their own code).
    if not code or not state or state != expected_state or not verifier:
        return RedirectResponse(f"{fe}/settings?openrouter=error")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                config.OPENROUTER_KEY_EXCHANGE_URL,
                json={"code": code, "code_verifier": verifier,
                      "code_challenge_method": "S256"},
            )
    except httpx.RequestError:
        return RedirectResponse(f"{fe}/settings?openrouter=error")
    if resp.status_code >= 400:
        return RedirectResponse(f"{fe}/settings?openrouter=error")
    key = (resp.json() or {}).get("key")
    if not key:
        return RedirectResponse(f"{fe}/settings?openrouter=error")
    db.set_openrouter_key(user["id"], crypto.encrypt(key))
    return RedirectResponse(f"{fe}/settings?openrouter=connected")


@router.post("/openrouter/disconnect")
async def openrouter_disconnect(request: Request, user=Depends(require_user)):
    db.clear_openrouter_key(user["id"])
    return JSONResponse({"ok": True})


# ---------- admin: approval queue ----------

@router.get("/admin/pending")
async def admin_list_pending(_: Request, admin=Depends(require_admin)):
    return {"pending": db.list_pending_users(), "max": _MAX_PENDING}


@router.get("/admin/firewall")
async def admin_firewall(_: Request, limit: int = 100, admin=Depends(require_admin)):
    """Recent AI-firewall events + aggregate counts. Detail holds only non-sensitive
    metadata (categories/tool/counts/snippet), never secret or PII values."""
    limit = max(1, min(limit, 500))
    return {
        "events": db.list_firewall_events(limit=limit),
        "counts": db.firewall_event_counts(),
    }


@router.get("/admin/firewall/policies")
async def admin_firewall_policies(_: Request, admin=Depends(require_admin)):
    """Per-user firewall policy overrides + the global defaults they inherit from.
    Powers the admin policy editor."""
    defaults = {name: bool(getattr(config, cfg))
                for name, cfg in firewall._CONFIG_FOR.items()}
    return {
        "users": db.list_users(),
        "policies": db.list_firewall_policies(),
        "defaults": defaults,
        "keys": list(firewall._CONFIG_FOR.keys()),
    }


@router.post("/admin/firewall/policy/{uid}")
async def admin_set_firewall_policy(uid: str, _: Request,
                                    patch: dict = Body(...),
                                    admin=Depends(require_admin)):
    """Set a user's firewall overrides. Each known key may be true/false (override)
    or null (clear → inherit the global default)."""
    if not db.get_user(uid):
        raise HTTPException(404, "user not found")
    clean = {k: (None if patch.get(k) is None else bool(patch.get(k)))
             for k in firewall._CONFIG_FOR if k in patch}
    return {"policy": db.set_firewall_policy(uid, clean)}


@router.post("/admin/approve/{uid}")
async def admin_approve(uid: str, _: Request, admin=Depends(require_admin)):
    target = db.get_user(uid)
    if not target:
        raise HTTPException(404, "user not found")
    if not target.get("is_pending"):
        # Already approved — still ensure their email is in the persistent
        # allowlist (handles users approved before this column existed).
        db.add_approved_email(target.get("email", ""), approved_by=admin["id"])
        db.clear_approval_token(uid)
        return {"ok": True, "already_approved": True}
    db.approve_user(uid)
    db.add_approved_email(target.get("email", ""), approved_by=admin["id"])
    db.clear_approval_token(uid)
    return {"ok": True}


@router.post("/admin/deny/{uid}")
async def admin_deny(uid: str, _: Request, admin=Depends(require_admin)):
    target = db.get_user(uid)
    if not target:
        raise HTTPException(404, "user not found")
    if target.get("is_admin"):
        raise HTTPException(400, "cannot deny an admin")
    if not target.get("is_pending"):
        raise HTTPException(400, "user is already approved; revoke not supported in this version")
    db.delete_user(uid)
    return {"ok": True}


# ---------- token-gated approval (ntfy push action targets) ----------
#
# These endpoints take no session — auth is the single-use token in the URL.
# Each token is bound to one specific pending user, so a leak only allows
# approving that one account, never escalating to others. Tokens expire 7 days
# after they're minted.

def _approve_via_token_inner(token: str) -> JSONResponse:
    target = db.get_user_by_approval_token(token)
    if not target:
        return JSONResponse({"ok": False, "error": "invalid or expired token"}, status_code=410)
    if target.get("is_admin"):
        # Defensive — shouldn't happen, admin starts approved.
        return JSONResponse({"ok": True, "already_approved": True})
    if not target.get("is_pending"):
        # Already approved (e.g. admin clicked the button twice). Idempotent.
        db.clear_approval_token(target["id"])
        db.add_approved_email(target.get("email", ""))
        return JSONResponse({"ok": True, "already_approved": True})
    db.approve_user(target["id"])
    db.add_approved_email(target.get("email", ""))
    db.clear_approval_token(target["id"])
    try:
        notify.push_action_confirmation(target.get("email", "user"), "approved")
    except Exception as e:  # noqa: BLE001
        print(f"[auth] confirm push failed: {e}", flush=True)
    return JSONResponse({"ok": True, "approved": True})


def _deny_via_token_inner(token: str) -> JSONResponse:
    target = db.get_user_by_approval_token(token)
    if not target:
        return JSONResponse({"ok": False, "error": "invalid or expired token"}, status_code=410)
    if target.get("is_admin"):
        return JSONResponse({"ok": False, "error": "cannot deny an admin"}, status_code=400)
    if not target.get("is_pending"):
        return JSONResponse({"ok": False, "error": "user is already approved; revoke not supported"}, status_code=400)
    email = target.get("email", "user")
    db.delete_user(target["id"])  # cascades to workspaces/conversations/etc.
    try:
        notify.push_action_confirmation(email, "denied")
    except Exception as e:  # noqa: BLE001
        print(f"[auth] confirm push failed: {e}", flush=True)
    return JSONResponse({"ok": True, "denied": True})


@router.post("/approve_via_token/{token}")
@router.get("/approve_via_token/{token}")
async def approve_via_token(token: str):
    return _approve_via_token_inner(token)


@router.post("/deny_via_token/{token}")
@router.get("/deny_via_token/{token}")
async def deny_via_token(token: str):
    return _deny_via_token_inner(token)
