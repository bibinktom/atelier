# OPENROUTER_OAUTH.md — Implementation plan

Goal: let each user **connect their own OpenRouter account in one button-press**
(OAuth PKCE), store their user-scoped API key encrypted, and route **every** LLM
call through that key — so inference is user-funded. NIM stays as an optional
operator-funded fallback. This is the first piece of Milestone 1 in `SCALING.md`.

Verified against current code (2026-06-10). All file:line anchors below are real.

---

## 0. How the flow works (PKCE, no client secret)

```
Browser                     Backend                         OpenRouter
   │  click "Connect"          │                                 │
   ├──GET /auth/openrouter/connect─▶                             │
   │                           │ make verifier+challenge(S256)   │
   │                           │ stash verifier+state in session │
   │  302 →openrouter.ai/auth?callback_url=…&code_challenge=…    │
   ├───────────────────────────────────────────────────────────▶│
   │                       user logs in (Google works) + Authorize│
   │  302 → /auth/openrouter/callback?code=…&state=…             │
   ◀────────────────────────────────────────────────────────────┤
   ├──GET /auth/openrouter/callback?code=…─▶                     │
   │                           │ verify state                    │
   │                           │ POST /api/v1/auth/keys           │
   │                           │   {code, code_verifier} ────────▶│
   │                           │ ◀──────────── {key:"sk-or-v1-…"} │
   │                           │ encrypt + store on user row      │
   │  302 → /settings?openrouter=connected                       │
   ◀───────────────────────────┤                                 │
```

Key facts (OpenRouter docs):
- Redirect target: `https://openrouter.ai/auth?callback_url=<URL>&code_challenge=<c>&code_challenge_method=S256`
- Exchange: `POST https://openrouter.ai/api/v1/auth/keys` body `{code, code_verifier}` → `{key: "sk-or-v1-…"}`
- `callback_url` **must be https on port 443 or 3000** (Cloudflare prod = ✅; local dev use :3000).
- Keys **don't expire**; user can revoke → calls return **401** → we prompt reconnect.

---

## 1. Prerequisites (env + deps)

**`backend/requirements.txt`** — add:
- `cryptography` (Fernet, for at-rest key encryption)

**`.env` / `.env.example`** — add:
```
KEY_ENCRYPTION_KEY=        # 32-byte urlsafe-base64 (Fernet.generate_key()). REQUIRED.
OPENROUTER_OAUTH_URL=https://openrouter.ai/auth
OPENROUTER_API_BASE=https://openrouter.ai/api/v1
OPENROUTER_KEY_EXCHANGE_URL=https://openrouter.ai/api/v1/auth/keys
ENABLE_NIM_FALLBACK=0       # 1 = operator-funded NIM when user not connected
DEFAULT_MODEL=meta-llama/llama-3.3-70b-instruct:free   # must be a valid OpenRouter id
```

> ⚠️ Losing `KEY_ENCRYPTION_KEY` makes every stored key undecryptable (users must
> reconnect). Treat it like `SESSION_SECRET`. Plan a rotation story (P4).

---

## 2. Backend changes (file by file)

### 2.1 `backend/app/crypto.py` — NEW (~20 LOC)
Fernet wrapper. Never log plaintext.
```python
from cryptography.fernet import Fernet
from . import config
_f = Fernet(config.KEY_ENCRYPTION_KEY.encode())
def encrypt(s: str) -> str: return _f.encrypt(s.encode()).decode()
def decrypt(tok: str) -> str: return _f.decrypt(tok.encode()).decode()
```

### 2.2 `backend/app/config.py`
- Read the new env vars (§1). `KEY_ENCRYPTION_KEY` is **required** (`os.environ[...]`).
- **Model catalog refactor** (`fetch_available_models`, currently config.py:235-255):
  repoint from NIM's `/models` to OpenRouter's `GET {OPENROUTER_API_BASE}/models`
  (public, no key). Map OpenRouter metadata → existing capability flags:
  - `supports_tools` ← `"tools" in model["supported_parameters"]`
  - `supports_images` ← `"image" in model["architecture"]["input_modalities"]`
  - Curate: keep an allowlist, but **surface `:free` variants first** (replace
    `_FEATURED_ORDER`). Keep NIM fetch behind `ENABLE_NIM_FALLBACK`.
- **Remap the hardcoded model ids** to OpenRouter ids (these are NIM ids today):
  | const | file:line | today (NIM) | → OpenRouter |
  |---|---|---|---|
  | `DEFAULT_MODEL` | config.py:199 | `qwen/qwen3-next-80b-a3b-instruct` | **`qwen/qwen3-next-80b-a3b-instruct:free`** (exact twin, tools ✓) |
  | arg-correct | chat.py:171 | `qwen/qwen3-next-80b-a3b-instruct` | same `:free` |
  | `PLANNER_MODEL` | config.py:44 | `openai/gpt-oss-120b` | **`openai/gpt-oss-120b:free`** (exact twin) |
  | `_SUMMARIZE_MODEL` | chat.py:482 | `openai/gpt-oss-20b` | **`openai/gpt-oss-20b:free`** (exact twin) |
  | reflect critic | chat.py:562 | `openai/gpt-oss-20b` | `openai/gpt-oss-20b:free` |
  | `MEMORY_EXTRACTOR_MODEL` | config.py:49 | `meta/llama-3.3-70b-instruct` | **`meta-llama/llama-3.3-70b-instruct:free`** (exact twin) |
  | `SPECIALIST_MODELS` research/reasoning | chat.py:975 | `openai/gpt-oss-120b` | `openai/gpt-oss-120b:free` |
  | `SPECIALIST_MODELS` quick | chat.py:975 | `openai/gpt-oss-20b` | `openai/gpt-oss-20b:free` |
  | `SPECIALIST_MODELS` code | chat.py:975 | `meta/llama-3.3-70b-instruct` | `meta-llama/llama-3.3-70b-instruct:free` ⚠️ tool-serving is provider-dependent (see below) |
  | `SPECIALIST_MODELS` vision | chat.py:975 | `meta/llama-3.2-90b-vision-instruct` | ⚠️ **no twin** → **`google/gemma-4-31b-it:free`** (vision+tools, 262k ctx) |
  | `SPECIALIST_MODELS` document | chat.py:975 | `mistralai/mistral-large-3-675b-instruct-2512` | ⚠️ **no free Mistral-Large** → free: `openai/gpt-oss-120b:free`; paid: `mistralai/mistral-large-2512` ($0.50/$1.50 per M) |

  > **Researched against OpenRouter's live `/models` API (339 models, 2026-06-10).**
  > Every existing NIM model except the two flagged has an exact `:free` twin that
  > advertises `tools` support. Caveat: the `tools` flag means OpenRouter *accepts*
  > the param, not that the model is reliable at tool-calling — **re-probe each
  > `:free` model under OpenRouter** before trusting it, exactly as `config.py`'s
  > dated `_NO_TOOL_PATTERNS` annotations did for NIM. Free models also route to
  > shared community capacity and can 429 independently of your own quota; the paid
  > twins are cheap and far more reliable (gpt-oss-20b $0.03/$0.14, gpt-oss-120b
  > $0.04/$0.18, llama-3.3-70b $0.10/$0.32 per M tokens).

  ### Tool-calling reliability (empirically probed 2026-06-10)
  Reliability = model capability × **provider-serving correctness** — and the
  metadata lies. A real probe (parallel 2-tool call, valid OpenAI `tool_calls`
  with parseable args) on NIM produced:

  | Model | Probe verdict | Implication |
  |---|---|---|
  | **Qwen3-next-80b** | ✅ clean **parallel** tool_calls, 1.3s; tops BFCL | **Use as the orchestrator default.** Best capability + serving. |
  | **Mistral-Large-3** | ✅ clean parallel, ~1s | great (big/paid) — good document specialist |
  | **llama-3.2-90b-vision** | ✅ tools work (2 calls) | `config.py supports_tools=False` is **stale** — flip it |
  | **gpt-oss-120b / 20b** | ⚠️ valid format but **single-call only** | by-design: a fix disabled parallel calls. **Fine for planner/summarizer/critic/quick (single- or no-tool roles); do NOT use as the orchestrator** which needs parallel `delegate`. Also defend against Harmony-token leakage (`<\|` split — already in chat.py). |
  | **llama-3.3-70b** | ❌ **FAIL on NIM** — emitted tool call as plain-text JSON (`finish_reason: stop`) | Model *is* capable (BFCL 0.773) but **NIM's serving breaks it**. Likely fine on Groq/Cerebras/OpenRouter, but **must be re-probed per provider** — don't assume. Prefer Qwen3 for the code specialist. |

  > **The llama-3.3-70b result is the headline lesson:** identical weights, broken
  > serving. Tool reliability must be probed **per (provider, model) pair**, not
  > assumed from a benchmark or a `tools` flag. A reusable probe ships in the repo:
  > **`tools/probe_toolcalling.py`** — set each provider's `*_API_KEY` env var and
  > run it; it prints a PASS/FAIL matrix and exits non-zero on any failure (so it
  > can gate a deploy). Run it against every provider+model before launch.

  ### ⚠️ Free-tier rate limits reshape the free UX (critical)
  OpenRouter `:free` models are capped at **20 requests/minute** and **50 requests/
  day** for users with <$10 credits (**1000/day** once they've bought $10+; failed
  calls still count). This collides head-on with the orchestrator's fan-out — one
  user message fires **15–30 LLM calls** (planner + up to 5 parallel delegates ×
  their own loops + per-result summarizers + reflect critic). Consequences:
  - A single rich turn can exceed **20 RPM mid-turn** → 429s → broken answer.
  - A no-credit user gets **~2 messages/day**; even a $10 user gets ~30–60/day.
  - **Therefore: free-tier users need a stripped "lite" pipeline** — disable the
    planner/reflect/summarizer and cap delegates to 0–1, collapsing a turn to a
    single-model tool loop (a handful of calls). This is the per-tier fan-out
    control already noted as P1 in `SCALING.md` — it's now a **launch requirement
    for free, not an optimization.** Also serialize (don't parallelize) sub-calls
    on `:free` models to respect 20 RPM.
  - Nudge users to add $10 OpenRouter credits (unlocks 1000/day + cheap, un-capped
    paid models) as the real path to a usable full-fan-out experience.
  - Sources: [OpenRouter rate limits](https://openrouter.ai/docs/api/reference/limits) ·
    [Free-tier limits explainer](https://openrouter.zendesk.com/hc/en-us/articles/39501163636379-OpenRouter-Rate-Limits-What-You-Need-to-Know)

### 2.3 `backend/app/db.py` — schema + helpers
- **Migration** (mirror the existing idempotent pattern at db.py:226-234): in
  `init_db()`, `ALTER TABLE users ADD COLUMN` for:
  - `openrouter_key_enc TEXT`
  - `openrouter_connected_at INTEGER`
- Add the columns to the `CREATE TABLE users` block (db.py:51) and the COLLATE-rebuild
  copy (db.py:251-261) so fresh installs + the rebuild path carry them.
- New helpers:
  - `set_openrouter_key(user_id, enc: str)` → UPDATE enc + connected_at=now()
  - `get_openrouter_key_enc(user_id) -> str | None`
  - `clear_openrouter_key(user_id)`
- **Never** put the key (enc or plain) in `get_user()`'s returned dict used by
  `/auth/me` — fetch it only in the LLM path.

### 2.4 `backend/app/auth.py` — OAuth endpoints (manual PKCE; do NOT use Authlib here)
Authlib is for OIDC; OpenRouter's flow is a bespoke redirect+exchange. Add:
```python
import base64, hashlib, secrets, httpx
from . import crypto

def _pkce_pair():
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge

@router.get("/openrouter/connect")
async def openrouter_connect(request: Request, user=Depends(require_user)):
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    request.session["or_verifier"] = verifier      # signed, httponly cookie
    request.session["or_state"] = state
    cb = f"{config.PUBLIC_BACKEND_URL}/auth/openrouter/callback"
    url = (f"{config.OPENROUTER_OAUTH_URL}?callback_url={cb}"
           f"&code_challenge={challenge}&code_challenge_method=S256&state={state}")
    return RedirectResponse(url)

@router.get("/openrouter/callback")
async def openrouter_callback(request: Request, code: str = "", state: str = "",
                              user=Depends(require_user)):
    if not code or state != request.session.pop("or_state", None):
        return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/settings?openrouter=error")
    verifier = request.session.pop("or_verifier", None)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(config.OPENROUTER_KEY_EXCHANGE_URL,
                         json={"code": code, "code_verifier": verifier})
    if r.status_code >= 400:
        return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/settings?openrouter=error")
    key = r.json().get("key")
    db.set_openrouter_key(user["id"], crypto.encrypt(key))
    return RedirectResponse(f"{config.PUBLIC_FRONTEND_URL}/settings?openrouter=connected")

@router.post("/openrouter/disconnect")
async def openrouter_disconnect(request: Request, user=Depends(require_user)):
    db.clear_openrouter_key(user["id"]); return JSONResponse({"ok": True})
```
- **`/auth/me`** (auth.py:96): add `"openrouter_connected": bool(get_openrouter_key_enc(user["id"]))`
  so the UI can show Connect vs Connected and gate chat.
- **State nonce** prevents callback CSRF (attacker injecting their own `code`).

### 2.5 `backend/app/nim.py` — provider credentials (the core refactor)
`route()` (nim.py:19) currently maps model→(base, key) using the **shared** NIM
key. Make the **per-user key** an explicit argument so it's impossible to silently
fall back to the wrong key.

```python
from dataclasses import dataclass

@dataclass
class LLMCreds:
    openrouter_key: str | None = None   # decrypted, per-user
    allow_nim_fallback: bool = False

def route(model: str, creds: "LLMCreds") -> tuple[str, str, str]:
    if model.startswith(_OLLAMA_PREFIX):            # local dev only
        return config.OLLAMA_BASE_URL, "ollama", model[len(_OLLAMA_PREFIX):]
    if creds.openrouter_key:
        return config.OPENROUTER_API_BASE, creds.openrouter_key, model
    if creds.allow_nim_fallback:
        return config.NVIDIA_BASE_URL, config.NVIDIA_API_KEY, model
    raise LLMNotConnected()                          # typed → UI prompts connect
```
- Add `creds: LLMCreds` param to `chat_once(...)` (nim.py:30) and `stream_chat(...)`
  (nim.py:57); pass it into `route(model, creds)`.
- Add `class LLMNotConnected(Exception)` and map a **401** from upstream to a
  `LLMKeyRevoked` exception so the orchestrator can emit a "reconnect" SSE error.
- Send OpenRouter's recommended attribution headers (`HTTP-Referer`, `X-Title`).

### 2.6 `backend/app/chat.py` — thread creds through all 9 call sites
1. **Resolve once per turn.** In `run_turn` (chat.py:1205, has `user`):
   ```python
   enc = db.get_openrouter_key_enc(user["id"])
   creds = nim.LLMCreds(
       openrouter_key=crypto.decrypt(enc) if enc else None,
       allow_nim_fallback=config.ENABLE_NIM_FALLBACK)
   ```
   Add a `creds: nim.LLMCreds` field to `@dataclass TurnState` (chat.py:593) and
   set it in the `TurnState(...)` constructor (chat.py:1295).
2. **Gate before streaming:** if `creds.openrouter_key is None and not
   allow_nim_fallback` → emit an SSE `error` event `{code:"not_connected"}` and stop.
3. **Pass `state.creds` (or `creds`) to every call site:**
   | call site | function |
   |---|---|
   | chat.py:70 | `_node_plan` planner |
   | chat.py:170 | arg auto-correct |
   | chat.py:517 | `_summarize_tool_result` |
   | chat.py:561 | reflect critic |
   | chat.py:661, 700 | `_node_act` main streams |
   | chat.py:1447, 1546 | sub-agent streams |
   Standalone helpers (`_summarize_tool_result`, `_node_reflect`, `_correct_args`)
   take `creds` as a new param; nodes read `state.creds`.
4. **Sub-agents:** `_run_subagent` (chat.py:1366, has `user`) resolves/receives the
   same `creds` and passes to its `nim.stream_chat` calls.
5. **Background memory extraction** (chat.py:1046): capture `creds` **before**
   scheduling and pass it in — `background.add_task` runs after the response, so a
   ContextVar would be gone. `memory.extract_from_conversation(..., creds=creds)`.
6. **Default model:** new conversations (chat.py:1023) default to an OpenRouter id;
   validate `body.model` against the OpenRouter catalog.

### 2.7 `backend/app/memory.py`
- `extract_from_conversation(...)` (memory.py:273) + `_call_extractor` (memory.py:229):
  add `creds` param; pass to `nim.route(model, creds)` / the httpx call.

### 2.8 `backend/app/main.py`
- No structural change. (When the catalog moves to async refresh in P2, the startup
  fetch moves into `lifespan`.)

---

## 3. Frontend changes

### 3.1 `frontend/lib/api.ts`
- `connectOpenRouterUrl: () => \`${BACKEND}/auth/openrouter/connect\`` (full-page nav,
  like `loginUrl`).
- `disconnectOpenRouter: () => jfetch("/auth/openrouter/disconnect", {method:"POST"})`.
- `me()` already returns the user; it now includes `openrouter_connected`.

### 3.2 NEW `frontend/app/settings/page.tsx`
- "AI provider" card: if `!openrouter_connected` → **Connect OpenRouter** button
  (`window.location.href = api.connectOpenRouterUrl()`); else → "Connected ✓" +
  Disconnect. Read `?openrouter=connected|error` query param → toast.
- Match the paper-and-ink aesthetic (Fraunces headings, brick primary button) per
  `CLAUDE.md` design constraints. Add a sidebar link.

### 3.3 `frontend/app/page.tsx` (chat) — gating
- If `me.openrouter_connected` is false (and no operator fallback), replace the
  composer with a "Connect your OpenRouter account to start chatting" prompt linking
  to `/settings`. Also handle the SSE `error{code:"not_connected"|"key_revoked"}`
  event (page.tsx SSE handler ~line 213) → show a reconnect CTA.

### 3.4 Onboarding
- After Google login, if not connected, route first-time users to `/settings` (or a
  one-step onboarding) so the very first action is connecting OpenRouter.

---

## 4. Security checklist
- [ ] Key encrypted at rest (Fernet); **never** logged, **never** sent to frontend.
- [ ] `KEY_ENCRYPTION_KEY` required at boot; documented rotation plan (P4).
- [ ] PKCE `state` nonce verified on callback (CSRF).
- [ ] `code_verifier` in signed httponly session cookie, single-use (`pop`).
- [ ] `callback_url` is the https backend URL (443/3000 only).
- [ ] 401 from OpenRouter → typed `LLMKeyRevoked` → reconnect prompt, not a 500.
- [ ] Key is user-scoped on OpenRouter; blast radius of a leak = that user's credits.
- [ ] Disconnect actually clears the column.

---

## 5. Testing plan
- **Unit:** `crypto.encrypt/decrypt` round-trip; `_pkce_pair()` challenge =
  base64url(sha256(verifier)) no padding; `route()` returns OpenRouter base+user key
  when connected, raises `LLMNotConnected` when not.
- **Mocked integration:** stub `OPENROUTER_KEY_EXCHANGE_URL` (respx/httpx mock) to
  return `{key:"sk-or-test"}`; assert callback stores an encrypted value and
  `/auth/me` flips `openrouter_connected`.
- **Live smoke:** with a real test OpenRouter account, connect → send a chat on a
  `:free` model → confirm a streamed answer and that **no NIM key** was used
  (temporarily unset `NVIDIA_API_KEY` with `ENABLE_NIM_FALLBACK=0`).
- **Revocation:** revoke the key in OpenRouter's dashboard mid-session → next turn
  surfaces the reconnect prompt.
- **Rebuild after every change:** `docker compose up --build` (running app is the
  image, not source).

---

## 6. Sequencing & compatibility
- Works on **current SQLite** (just adds columns) — does **not** block on the
  Postgres migration, though they're both Milestone 1.
- Ship behind `ENABLE_NIM_FALLBACK=1` first so existing family users keep working
  while the OpenRouter path is validated; flip to `0` for public launch.
- Rough size: backend ~250–350 LOC (crypto + 4 endpoints + provider refactor +
  threading), frontend ~150 LOC (settings page + gating). The **threading through
  the 9 call sites + the catalog/model-id remap** is the bulk of the effort and the
  main risk surface.

---

## 7. Open decisions
1. **Operator fallback for non-connected users?** (`ENABLE_NIM_FALLBACK`) — free
   trial on operator's dime vs. hard gate until connected.
2. ~~**Model id remapping**~~ — ✅ **RESEARCHED** (see §2.2). All but vision/document
   have exact `:free` twins with tool support. Default = `qwen/qwen3-next-80b-a3b-
   instruct:free`. Remaining sub-decision: re-probe each `:free` twin for *real*
   tool-calling reliability before launch (the `tools` flag is necessary, not
   sufficient).
3. ~~**Fan-out on free tier**~~ — ✅ **ANSWERED & escalated** (see §2.2 rate-limit
   box). The 20 RPM / 50–1000 per-day free caps make a "lite" stripped pipeline
   (no planner/reflect/summarizer, 0–1 delegates, serialized sub-calls) a
   **launch requirement** for free users, not a tuning option.
4. **Settings page vs. extend `/identity`** — new `/settings` recommended.

---

*Plan generated 2026-06-10 against verified code. Keep in sync with `SCALING.md`
and `CLAUDE.md`.*
