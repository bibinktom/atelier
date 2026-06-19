import os
import secrets

import httpx

# Local desktop build (Tauri app on the user's own machine, ATELIER_LOCAL=1):
# single-user, OpenRouter-funded inference, no Google OAuth, tools run natively on
# the host. The secrets that are mandatory for the shared server (NVIDIA / Google /
# session) are optional here — see _required().
ATELIER_LOCAL = os.environ.get("ATELIER_LOCAL", "0") not in {"0", "false", "False", ""}
# Confirm-before-running gate for destructive/device commands (local build only).
PERMISSIONS_ENABLED = os.environ.get("PERMISSIONS_ENABLED", "1") not in {"0", "false", "False", ""}
# Filesystem root the local agent may operate under (default: the user's home dir).
# Mirrors tools/app/workspace.py's ATELIER_LOCAL_ROOT; used here to seed a default
# workspace and to confine user-picked project folders.
ATELIER_LOCAL_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("ATELIER_LOCAL_ROOT") or "~"))


def _required(name: str, local_default: str = "") -> str:
    """Like os.environ[name] (fail-fast) for the shared server, but in local mode
    falls back to a default so the desktop build boots without server secrets."""
    v = os.environ.get(name)
    if v:
        return v
    if ATELIER_LOCAL:
        return local_default
    raise KeyError(name)


NVIDIA_API_KEY = _required("NVIDIA_API_KEY", "local-unused")
NVIDIA_BASE_URL = os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# ---- ollama (local) ----
# Optional. Models discovered from this endpoint are exposed in the picker with
# the prefix `ollama/<id>`. Set to empty string to disable. host.docker.internal
# resolves via extra_hosts in docker-compose.yml.
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434/v1").rstrip("/")

# ---- skills catalog (GitHub discovery) ----
# A daily background job searches public GitHub repositories for Claude-style
# SKILL.md files and populates a shared, browsable catalog that users can install
# from with one click. See backend/app/catalog.py.
#
# SKILLS_CATALOG_ENABLED  — master switch (default on).
# GITHUB_TOKEN            — optional. We use the *unauthenticated* repo-search API
#                           by default (~60 req/hr, plenty for once-daily). A token
#                           raises the limit to 5000 req/hr and is the ONLY thing
#                           passed to api.github.com — never to any model endpoint.
# SKILLS_CATALOG_CRON     — 5-field cron for the daily refresh (UTC).
# SKILLS_CATALOG_QUERIES  — ';'-separated GitHub repo-search queries. (Semicolon,
#                           not comma — the `in:a,b,c` qualifier already uses commas.)
# SKILLS_CATALOG_MAX_REPOS / _MAX_FILES_PER_REPO / _MAX_SKILLS — fan-out caps.
# SKILLS_CATALOG_MAX_PER_REPO — max skills *stored* from any single repo, so one
#                           mega-repo can't flood the catalog (diversity guard).
SKILLS_CATALOG_ENABLED = os.environ.get("SKILLS_CATALOG_ENABLED", "1") not in {"0", "false", "False", ""}
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
SKILLS_CATALOG_CRON = os.environ.get("SKILLS_CATALOG_CRON", "0 6 * * *").strip()  # 06:00 UTC daily
SKILLS_CATALOG_QUERIES = [
    q.strip() for q in os.environ.get(
        "SKILLS_CATALOG_QUERIES",
        "claude skill in:name,description,readme,topics;"
        "agent skill in:name,description,readme,topics;"
        "claude code skill in:name,description,readme,topics;"
        "anthropic skill in:name,description,readme,topics;"
        "awesome claude skills in:name,description,readme,topics;"
        "SKILL.md in:name,readme,topics",
    ).split(";") if q.strip()
]
SKILLS_CATALOG_MAX_REPOS = int(os.environ.get("SKILLS_CATALOG_MAX_REPOS", "40"))
SKILLS_CATALOG_MAX_FILES_PER_REPO = int(os.environ.get("SKILLS_CATALOG_MAX_FILES_PER_REPO", "40"))
SKILLS_CATALOG_MAX_PER_REPO = int(os.environ.get("SKILLS_CATALOG_MAX_PER_REPO", "5"))
SKILLS_CATALOG_MAX_SKILLS = int(os.environ.get("SKILLS_CATALOG_MAX_SKILLS", "400"))

GOOGLE_CLIENT_ID = _required("GOOGLE_CLIENT_ID", "local-unused")
GOOGLE_CLIENT_SECRET = _required("GOOGLE_CLIENT_SECRET", "local-unused")

ALLOWED_EMAILS = {
    e.strip().lower() for e in os.environ.get("ALLOWED_EMAILS", "").split(",") if e.strip()
}
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "").strip().lower()

# Self-serve signup: when 1, any verified Google account is admitted immediately
# (no pending/admin-approval queue). Inference is still gated on the user connecting
# their own OpenRouter account, so open signup costs the operator nothing. Set 0 to
# keep the old invite/approval model. ALLOWED_EMAILS (if set) still pre-approves.
OPEN_SIGNUP = os.environ.get("OPEN_SIGNUP", "1") not in {"0", "false", "False", ""}

# Per-user disk quota across all their project folders (/workspaces/<user_id>/...).
# Enforced by the tools sidecar on writes/clones; surfaced via GET /workspaces/usage.
USER_QUOTA_BYTES = int(os.environ.get("USER_QUOTA_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2 GiB

# ---- AI firewall (LLM Guard sidecar) ----
# Scans the user message on the way in (block prompt-injection/jailbreak) and the
# model's answer on the way out (redact leaked secrets). Runs in the `guard`
# sidecar; backend client is firewall.py.
#   FIREWALL_ENABLED       — master switch.
#   GUARD_URL              — sidecar base URL (compose injects http://guard:8003).
#   FIREWALL_FAIL_OPEN     — when the sidecar is unreachable: 1 = don't block chat
#                            (availability > filter), 0 = hard-gate the turn.
#   FIREWALL_TOOL_SCAN     — scan untrusted tool results (web_fetch/web_search/
#                            codebase_search) for indirect prompt injection; on a hit
#                            the orchestrator is warned, not blocked (defang).
#   FIREWALL_CODE_SCAN     — scan LLM-written/cloned code (workspace_write/apply_patch)
#                            with CodeShield; advisory warning to the coder.
#   FIREWALL_PII_OUTPUT    — (read by the guard sidecar) redact PII from answers.
#   FIREWALL_BUFFER_OUTPUT — (phase 3) hold the final answer back and scan it BEFORE
#                            the user sees any of it, closing the "secret briefly
#                            visible in-flight" gap. Trade-off: the final answer
#                            appears all-at-once instead of token-by-token (tool/
#                            plan/reflect events still stream live).
#   FIREWALL_ALIGNMENT_CHECK — (phase 3) after a tool-using turn, an LLM critic audits
#                            whether the agent's actions still serve the user's request
#                            (goal-drift / injection-hijack detection). Defang: warn +
#                            log by default; set FIREWALL_ALIGNMENT_BLOCK=1 to withhold
#                            the answer on a misalignment. Runs in the backend (it needs
#                            the user's OpenRouter creds), not the guard sidecar.
# Per-user overrides live in the `firewall_policy` table (NULL column = inherit the
# global default below); resolved per turn and threaded via firewall.using_policy().
FIREWALL_ENABLED = os.environ.get("FIREWALL_ENABLED", "1") not in {"0", "false", "False", ""}
FIREWALL_FAIL_OPEN = os.environ.get("FIREWALL_FAIL_OPEN", "1") not in {"0", "false", "False", ""}
FIREWALL_TOOL_SCAN = os.environ.get("FIREWALL_TOOL_SCAN", "1") not in {"0", "false", "False", ""}
FIREWALL_CODE_SCAN = os.environ.get("FIREWALL_CODE_SCAN", "1") not in {"0", "false", "False", ""}
FIREWALL_PII_OUTPUT = os.environ.get("FIREWALL_PII_OUTPUT", "1") not in {"0", "false", "False", ""}
FIREWALL_BUFFER_OUTPUT = os.environ.get("FIREWALL_BUFFER_OUTPUT", "1") not in {"0", "false", "False", ""}
FIREWALL_ALIGNMENT_CHECK = os.environ.get("FIREWALL_ALIGNMENT_CHECK", "1") not in {"0", "false", "False", ""}
FIREWALL_ALIGNMENT_BLOCK = os.environ.get("FIREWALL_ALIGNMENT_BLOCK", "0") not in {"0", "false", "False", ""}
# Model for the goal-drift critic (OpenRouter id; nim maps it). Reuses the cheap
# reflect-tier model by default.
ALIGNMENT_MODEL = os.environ.get("ALIGNMENT_MODEL", "openai/gpt-oss-20b:free")

SESSION_SECRET = _required("SESSION_SECRET", secrets.token_urlsafe(32))

# ---- per-user provider keys (OpenRouter OAuth) ----
# Fernet key used by crypto.py to encrypt each user's OpenRouter API key at rest.
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Required — losing it invalidates all stored keys (users reconnect).
KEY_ENCRYPTION_KEY = _required("KEY_ENCRYPTION_KEY", "")
if not KEY_ENCRYPTION_KEY:
    # Local mode and no key supplied: generate an ephemeral, valid-format Fernet key
    # (urlsafe base64, 32 bytes). The launcher normally persists a stable one; this
    # fallback just means a stored OpenRouter key won't survive a restart (reconnect).
    import base64
    KEY_ENCRYPTION_KEY = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

# OpenRouter OAuth (PKCE) endpoints. Users connect their own account in one click;
# the app uses their user-scoped key so inference is user-funded.
OPENROUTER_OAUTH_URL = os.environ.get("OPENROUTER_OAUTH_URL", "https://openrouter.ai/auth").rstrip("/")
OPENROUTER_API_BASE = os.environ.get("OPENROUTER_API_BASE", "https://openrouter.ai/api/v1").rstrip("/")
OPENROUTER_KEY_EXCHANGE_URL = os.environ.get(
    "OPENROUTER_KEY_EXCHANGE_URL", "https://openrouter.ai/api/v1/auth/keys").rstrip("/")

# When 1, users who haven't connected OpenRouter fall back to the operator-funded
# NVIDIA NIM key (e.g. a free trial, or to keep existing family users working).
# When 0, chat is hard-gated until the user connects a provider.
ENABLE_NIM_FALLBACK = os.environ.get("ENABLE_NIM_FALLBACK", "0") not in {"0", "false", "False", ""}

# ---- lite pipeline ----
# Free models are capped at ~20 requests/minute. The full orchestrator fans out
# 15-30 LLM calls per turn (planner + parallel delegates + per-result summarizers
# + reflect critic), which blows that cap — especially on web-search turns. The
# "lite" pipeline collapses a turn to a single-model tool loop: no planner, no
# reflect, no summarizer, and delegate disabled (the model runs its own
# search/fetch loop instead of spawning a swarm of sub-agents).
#   auto (default) -> lite when the model id ends with ":free"
#   on             -> always lite
#   off            -> always full
LITE_PIPELINE = os.environ.get("LITE_PIPELINE", "auto").strip().lower()


def is_lite_model(model: str) -> bool:
    if LITE_PIPELINE == "on":
        return True
    if LITE_PIPELINE == "off":
        return False
    return model.endswith(":free")

PUBLIC_FRONTEND_URL = os.environ.get("PUBLIC_FRONTEND_URL", "http://localhost:3000").rstrip("/")
PUBLIC_BACKEND_URL = os.environ.get("PUBLIC_BACKEND_URL", "http://localhost:8000").rstrip("/")

TOOLS_URL = os.environ.get("TOOLS_URL", "http://tools:8001").rstrip("/")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:////data/app.db")
DB_PATH = DATABASE_URL.replace("sqlite:///", "")

FILES_DIR = os.environ.get("FILES_DIR", "/files")
WORKSPACES_DIR = os.environ.get("WORKSPACES_DIR", "/workspaces")

# Desktop build: a directory of the statically-exported Next.js frontend. When set
# (and present), the backend serves the UI itself, so the Tauri webview loads a
# single origin (UI + API) — no separate frontend server, no CORS.
FRONTEND_DIST = os.environ.get("FRONTEND_DIST", "").strip()


# ---- planner ----
# Pre-flight planning step: before the orchestrator's tool-call loop, a thinking
# model reads the user's prompt and produces a short plan that gets injected as
# extra system context. Reduces hallucination and tool-call thrash.
PLANNER_ENABLED = os.environ.get("PLANNER_ENABLED", "1") not in {"0", "false", "False", ""}
# gpt-oss-120b chosen after probing NIM thinking models on 2026-05-04: it produces
# a clean numbered plan in `content` AND exposes a separate `reasoning_content`
# field. qwen3-next-80b-a3b-thinking and kimi-k2-thinking either leak CoT into
# content or time out under the planner's latency budget.
PLANNER_MODEL = os.environ.get("PLANNER_MODEL", "openai/gpt-oss-120b:free")

# ---- memory extractor ----
# Post-conversation memory extractor. Set to an `ollama/<id>` model id to keep
# transcript content on the LAN; otherwise it goes to NIM.
MEMORY_EXTRACTOR_MODEL = os.environ.get("MEMORY_EXTRACTOR_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

# ---- ntfy push ----
# Admin gets a push notification when a new family member signs in and lands in
# the pending queue. The notification carries Approve/Deny inline buttons that
# hit /auth/approve_via_token / /auth/deny_via_token over the Cloudflare tunnel.
# Topic shared with the existing intrusion-monitor topic on the same host.
# Set NTFY_TOPIC="" to disable. Default points at the topic already subscribed
# in the user's ntfy app (auto-generated by /media/bibintom/4tb/docker/immich).
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "superserver-PsGy4_JcoaA").strip()


# ---- model catalog ----
#
# Capability hints. Anything not listed defaults to text-only, no tools.
# Update this map as NIM changes; everything else is sourced live from the catalog.
_VISION_PATTERNS = ("vision", "vl-", "multimodal", "fuyu", "deplot", "neva", "kosmos", "phi-3-vision", "phi-4-multimodal")
_NO_TOOL_PATTERNS = (
    # Reasoning / thinking heads emit internal tokens not OpenAI-format tool calls.
    "kimi-k2-thinking", "qwen3-next-80b-a3b-thinking", "magistral", "deepseek-r1", "reasoning",
    # Empirically broken tool-calling on NIM as of testing 2026-05-04 — keep them for plain chat only.
    "deepseek-v4-flash",       # truncates tool-arg JSON
    "deepseek-v4-pro",         # tool calls OK but never produces final text
    "kimi-k2.6", "kimi-k2-instruct-0905",  # empty / very slow replies under tools
    "kimi-k2-instruct",        # same family
    "llama-4-maverick",        # emits tool calls as plain JSON text not OpenAI-format
    "qwen2.5-coder-32b",       # NIM disables tools on this model: 400 "Tool use has not been enabled"
    "mistral-medium-3.5",      # times out >90s with tools
    # qwen3-coder-480b: re-probed 2026-05-04 — single-tool, parallel-tool, and plain-chat all PASS
    # with structured tool_calls and coherent final answers. Promoted to picker.
)
_DEFAULT_TOOL_VENDORS = ("deepseek-ai/", "meta/llama-3.3", "meta/llama-4", "meta/llama-3.1", "nvidia/llama-3.3-nemotron-super",
                        "nvidia/llama-3.1-nemotron-ultra", "nvidia/llama-3.1-nemotron-70b", "nvidia/nemotron-3", "nvidia/nemotron-nano-3",
                        "qwen/qwen3", "qwen/qwen2.5", "mistralai/mistral-large", "mistralai/mistral-medium", "mistralai/mistral-small",
                        "mistralai/devstral", "mistralai/ministral", "mistralai/mistral-nemotron",
                        "openai/gpt-oss", "moonshotai/kimi", "minimaxai/minimax", "google/gemma-3", "google/gemma-4",
                        "ai21labs/jamba", "stepfun-ai/step", "ibm/granite-3", "databricks/dbrx", "bytedance/seed-oss")

# Models that should never appear in the picker (utility, embedding, classifier, content safety).
_DROP_PATTERNS = (
    "embed", "rerank", "nemoguard", "content-safety", "topic-control", "jailbreak", "safety-guard",
    "retriever", "detection", "ocr", "speech", "tts", "asr", "video", "audio", "image-gen",
    "stable", "sdxl", "flux", "clip", "translate", "calibration", "parse", "pii", "gliner",
    "guard", "reward", "chatqa",
)

# Curated picker. Only these IDs (and any that resolve to them via a known fallback) are
# exposed to family members — the full 100+ NIM catalog is too noisy and includes 1B/utility
# models a non-technical user can easily pick by accident.
_PICKER_ALLOWLIST = {
    # Verified tool-callers (PASS in 2026-05-04 probe), fastest first
    "openai/gpt-oss-20b",
    "qwen/qwen3-next-80b-a3b-instruct",
    "openai/gpt-oss-120b",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "google/gemma-4-31b-it",
    "mistralai/mistral-large-3-675b-instruct-2512",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    # Vision (image-aware)
    "meta/llama-3.2-90b-vision-instruct",
    # Chat-only fallbacks (broken under tools — kept here for plain chat selection)
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "moonshotai/kimi-k2.6",
    "moonshotai/kimi-k2-thinking",
}

# Featured order within the allowlist (otherwise alphabetical).
_FEATURED_ORDER = (
    "openai/gpt-oss-20b",
    "qwen/qwen3-next-80b-a3b-instruct",
    "openai/gpt-oss-120b",
    "qwen/qwen3.5-122b-a10b",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "google/gemma-4-31b-it",
    "mistralai/mistral-large-3-675b-instruct-2512",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "meta/llama-3.2-90b-vision-instruct",
    "deepseek-ai/deepseek-v4-flash",
    "deepseek-ai/deepseek-v4-pro",
    "moonshotai/kimi-k2.6",
    "moonshotai/kimi-k2-thinking",
)


def _label_for(mid: str) -> str:
    parts = mid.split("/", 1)
    name = parts[1] if len(parts) == 2 else parts[0]
    pretty = (
        name.replace("-instruct", "").replace("-it", "").replace("_", " ")
        .replace("deepseek-", "DeepSeek ")
        .replace("llama-", "Llama ").replace("llama3-", "Llama 3 ").replace("llama2-", "Llama 2 ")
        .replace("qwen", "Qwen").replace("gemma-", "Gemma ").replace("phi-", "Phi ")
        .replace("mistral-", "Mistral ").replace("mixtral-", "Mixtral ").replace("nemotron-", "Nemotron ")
        .replace("kimi-", "Kimi ").replace("gpt-oss-", "GPT-OSS ")
        .replace("granite-", "Granite ").replace("codestral-", "Codestral ").replace("starcoder", "StarCoder")
        .replace("magistral-", "Magistral ").replace("devstral-", "Devstral ").replace("ministral-", "Ministral ")
        .replace("minimax-", "MiniMax ")
        .replace("-", " ")
    )
    pretty = " ".join(w.capitalize() if w.islower() and len(w) > 2 and not w[0].isdigit() else w for w in pretty.split())
    return pretty.strip()


def _featured_index(mid: str) -> int:
    for i, prefix in enumerate(_FEATURED_ORDER):
        if mid.startswith(prefix):
            return i
    return len(_FEATURED_ORDER) + 1


def _build_entry(mid: str) -> dict | None:
    # Curated picker — only show models on the explicit allowlist.
    if mid not in _PICKER_ALLOWLIST:
        return None
    low = mid.lower()
    if any(p in low for p in _DROP_PATTERNS):
        return None
    supports_images = any(p in low for p in _VISION_PATTERNS)
    if any(p in low for p in _NO_TOOL_PATTERNS):
        supports_tools = False
    else:
        supports_tools = any(low.startswith(p) for p in _DEFAULT_TOOL_VENDORS)
    return {
        "id": mid,
        "label": _label_for(mid),
        "supports_tools": supports_tools,
        "supports_images": supports_images,
    }


# ---- OpenRouter curated picker (primary catalog) ----
# Each user funds inference with their own OpenRouter key (OAuth). Curated +
# tool-calling-probed 2026-06-10 (see tools/probe_toolcalling.py and
# OPENROUTER_OAUTH.md). `:free` variants first; re-probe before adding models.
# Under NIM fallback, nim.route() strips `:free` and normalizes the vendor prefix.
_OPENROUTER_PICKER = [
    {"id": "qwen/qwen3-next-80b-a3b-instruct:free",  "label": "Qwen3 Next 80B (free)",       "supports_tools": True,  "supports_images": False},
    {"id": "openai/gpt-oss-120b:free",               "label": "GPT-OSS 120B (free)",         "supports_tools": True,  "supports_images": False},
    {"id": "openai/gpt-oss-20b:free",                "label": "GPT-OSS 20B (free)",          "supports_tools": True,  "supports_images": False},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "label": "Llama 3.3 70B (free)",        "supports_tools": True,  "supports_images": False},
    {"id": "google/gemma-4-31b-it:free",             "label": "Gemma 4 31B · vision (free)", "supports_tools": True,  "supports_images": True},
    {"id": "qwen/qwen3-coder:free",                  "label": "Qwen3 Coder (free)",          "supports_tools": True,  "supports_images": False},
    # Cheap paid options — no free-tier rate caps, more reliable:
    {"id": "mistralai/mistral-large-2512",           "label": "Mistral Large · vision",      "supports_tools": True,  "supports_images": True},
]

# Used if the live OpenRouter catalog fetch fails at startup.
FALLBACK_MODELS = list(_OPENROUTER_PICKER)

# Default model for new conversations — a free, tool-capable OpenRouter model.
# Qwen3 Next was the best parallel tool-caller in the 2026-06-10 probe. Overridable
# via DEFAULT_MODEL; users pick anything else from the dropdown per conversation.
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "qwen/qwen3-next-80b-a3b-instruct:free")


def _fetch_ollama_models() -> list[dict]:
    """List local Ollama models. Empty list on failure (Ollama not running, etc.)."""
    if not OLLAMA_BASE_URL:
        return []
    try:
        resp = httpx.get(f"{OLLAMA_BASE_URL}/models", timeout=3.0)
        resp.raise_for_status()
        items = resp.json().get("data", [])
    except Exception as e:  # noqa: BLE001 — best-effort discovery
        print(f"[config] ollama discovery skipped: {e}")
        return []
    out: list[dict] = []
    for m in items:
        mid = m.get("id")
        if not mid:
            continue
        low = mid.lower()
        is_vision = any(p in low for p in _VISION_PATTERNS)
        # Tool-calling availability depends on the model. Ollama's OpenAI-compat
        # forwards tool calls for models that natively support them; others will
        # ignore the tools field. Default to True so the picker exposes them;
        # broken models can be opt-out via _NO_TOOL_PATTERNS by name.
        no_tools = any(p in low for p in _NO_TOOL_PATTERNS)
        out.append({
            "id": f"ollama/{mid}",
            "label": f"Ollama · {mid}",
            "supports_tools": not no_tools,
            "supports_images": is_vision,
        })
    out.sort(key=lambda e: e["id"])
    return out


def _fetch_openrouter_models() -> list[dict]:
    """Return the curated OpenRouter picker, filtered to ids OpenRouter currently
    serves. The /models endpoint is public (no key needed). Falls back to the full
    curated list if the fetch fails."""
    try:
        resp = httpx.get(f"{OPENROUTER_API_BASE}/models", timeout=10.0)
        resp.raise_for_status()
        live = {m.get("id") for m in resp.json().get("data", [])}
    except Exception as e:  # noqa: BLE001 — startup convenience
        print(f"[config] OpenRouter catalog fetch failed, using curated list: {e}")
        return list(_OPENROUTER_PICKER)
    avail = [m for m in _OPENROUTER_PICKER if m["id"] in live]
    return avail or list(_OPENROUTER_PICKER)


def fetch_available_models() -> list[dict]:
    """Startup catalog. OpenRouter (user-funded, per-user keys) is the primary
    provider; local Ollama models (if any) are appended after."""
    return _fetch_openrouter_models() + _fetch_ollama_models()


AVAILABLE_MODELS = fetch_available_models()
