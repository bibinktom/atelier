# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# Atelier — family AI workspace

A self-hosted, Google-OAuth-gated chat workspace for a small set of family members.
LLMs are served via NVIDIA NIM (build.nvidia.com); tools (web search, web fetch,
PDF / xlsx / pptx / flyer generation, sandboxed workspace shell) run in an isolated container.

## Run / build

```
cp .env.example .env   # then fill in the secrets
docker compose up --build
```

There are no host ports — the frontend and backend are reachable only through the
external Docker network `family-ai-public` (a Cloudflare tunnel attaches to it).
For local browser testing, attach a temporary `ports:` mapping in
`docker-compose.yml` or run the services natively. **Always rebuild after code
edits** (`docker compose up --build`); the running containers are images, not
bind-mounted source. Frontend dev outside Docker: `cd frontend && npm run dev`
(Next.js 15 / React 19 / Tailwind v4).

## Architecture

```
┌──────────┐  external net  ┌──────────────┐  internal net  ┌──────────────┐
│ frontend │◀─family-ai────▶│   backend    │───────────────▶│    tools     │
│ Next.js  │     public     │   FastAPI    │                │  FastAPI     │
└──────────┘                │  SQLite +    │                │  sandbox     │
                            │  sessions    │                └──────────────┘
                            └──────────────┘                       │
                                  │                                ▼
                                  ▼                       ┌────────────────┐
                            ┌─────────┐                   │ /files (gen)   │
                            │ NIM API │                   │ /workspaces    │
                            └─────────┘                   └────────────────┘
```

Five Compose services:
- **frontend** (`/frontend`) — Next.js 15 App Router + Tailwind v4. No host port.
- **backend** (`/backend`) — FastAPI. Google OAuth, signed-cookie sessions, NIM
  streaming with the orchestrator state graph, SQLite, file downloads,
  per-user memory + skills. Holds Google/NIM/Tavily/Session secrets. No host port.
- **tools** (`/tools`) — FastAPI sidecar. Hardened container (`cap_drop: ALL`,
  `no-new-privileges`, mem cap, `pids_limit`, tmpfs `/tmp`). Runs as host
  uid/gid `1000:1000` so files written into `/workspaces` are owned by the host
  user (editable from a normal file manager). Internal network only — the host
  cannot reach it.
- **preview** (`/preview`) — LibreOffice-in-a-sandbox PDF converter on the
  internal network only. Backend posts Office docs (`xlsx`, `pptx`, `docx`,
  etc.) to `PREVIEW_URL` (default `http://preview:8002`); the sidecar returns
  the PDF in the response body and holds no state. Mounts `/files` and
  `/workspaces` **read-only** so even an RCE in LibreOffice's parsers can't
  tamper with canonical files or reach backend secrets. Conversion cache lives
  at `/tmp/preview-cache/` inside the **backend** container, keyed by source
  inode + mtime + size. See `backend/app/preview.py` for the client.
- **guard** (`/guard`) — AI firewall. LLM Guard scanners behind FastAPI on the
  internal network only. Backend posts text to `GUARD_URL` (default
  `http://guard:8003`): `/scan/input` blocks prompt-injection/jailbreak on the
  user message, `/scan/output` redacts leaked secrets from the answer. The
  **lowest-privilege service in the stack** — no secrets, **no volume mounts** (it
  only ever sees plain text over HTTP). Model weights (DeBERTa prompt-injection +
  toxicity) are **baked into the image at build**, so it needs no egress and runs
  `read_only`. Client is `backend/app/firewall.py`; hooked into the orchestrator
  in `chat.py` (input scan in `run_turn` before the node loop, output scan after).

Networks:
- `family-ai-public` (external) — frontend ↔ backend, also where Cloudflare tunnel attaches.
- `internal` — backend ↔ tools, backend ↔ preview, backend ↔ guard (none on `family-ai-public`).

Volumes:
- `app-data` — SQLite DB.
- `generated-files` (`/files`) — produced PDFs/xlsx/pptx/flyers + uploaded images.
  Shared backend↔tools. The backend `lifespan` hook chmods this 0o777 on boot
  because the volume is created root-owned but tools runs as uid 1000.
- Host bind mount `${WORKSPACES_HOST_DIR}` → `/workspaces` (default
  `/media/bibintom/4tb/familyai-workspaces`) — per-user, per-project scratch dirs.

## Auth

Google OAuth (Authlib). Allowlist in `ALLOWED_EMAILS`. Sessions are signed
cookies (`SessionMiddleware`, cookie `famai_sid`, `same_site=lax`, `httponly`,
`https_only` set automatically when `PUBLIC_BACKEND_URL` starts with `https://`).
`is_admin` is derived per login from `ADMIN_EMAIL` (no mutable DB flag).

## Models

`backend/app/config.py:fetch_available_models` runs at startup against
`${NVIDIA_BASE_URL}/models`, then filters through `_PICKER_ALLOWLIST` and
applies capability heuristics:
- `_VISION_PATTERNS` → `supports_images`
- `_NO_TOOL_PATTERNS` → forces `supports_tools=False` (reasoning/thinking heads
  that emit raw CoT tokens, plus models empirically broken under tool-calling
  on NIM as of 2026-05-04 — keep the dated annotations honest when re-probing).
- `_DEFAULT_TOOL_VENDORS` prefix match → `supports_tools=True`.

If the live fetch fails, `FALLBACK_MODELS` is used. **Default**:
`qwen/qwen3-next-80b-a3b-instruct` (fast, OpenAI-format tool calls, no Harmony
special-token leakage). `_FEATURED_ORDER` controls picker ordering.

When changing the allowlist or default, verify IDs against
`https://docs.api.nvidia.com/nim/reference/llm-apis` or
`https://build.nvidia.com/models`. Don't add models you haven't probed.

## Orchestrator state graph (`backend/app/chat.py`)

`post_message` runs a node dispatcher: **plan → act → (reflect ↔ act) → respond**.
SSE events are emitted as nodes execute.

- **plan** (`_node_plan`) — pre-flight planner. Skipped for short messages or
  when no tools are available. Calls `PLANNER_MODEL` (default
  `openai/gpt-oss-120b`) for a 3–7 step plan, injects it into the system prompt,
  and emits a `plan` SSE event for the UI's `PlanChip`. Disable with
  `PLANNER_ENABLED=0`.
- **act** (`_node_act`) — one orchestrator hop. Streams text and accumulates
  `tool_calls`; if any tool calls were emitted, runs them **in parallel** via
  `asyncio.gather(_exec_tool)`. After execution, summaries are computed in
  parallel for `web_search` / `web_fetch` / `delegate` results above
  `_SUMMARIZE_THRESHOLD` (1500 chars) using `openai/gpt-oss-20b`; the summary
  goes to the orchestrator on the next hop instead of the raw payload, but the
  full result is still persisted and surfaced to the UI. Loops back to `act`
  until no tool calls. Hard cap `max_hops=8`.
- **reflect** (`_node_reflect`) — runs once per turn, only if tools were used.
  A critic LLM (`openai/gpt-oss-20b`) audits the draft answer against tool
  results and returns `{ok, issues}`. On `ok=false`, the rejected draft row is
  deleted from the DB (so corrected reply doesn't sit next to the hallucinated
  one), a critic note is appended to the system prompt, and control bounces
  back to `act`. Emits `reflect` SSE event.

Per-turn machinery inside `act`:
- **LRU cache** (`CACHEABLE_TOOLS = {web_search, web_fetch}`) — pure-functional
  results within a turn are cached by `(name, json(args))`. Generators / file-
  producing / state-mutating tools are NEVER cached.
- **Validation auto-correct** — when a tool returns a 422-shaped error,
  `_correct_args` asks `qwen/qwen3-next-80b-a3b-instruct` to reshape the args
  using the per-tool hints in `_TOOL_HINTS` (e.g. xlsx rows must be arrays not
  objects), then retries once.
- **Harmony-token cleanup** — GPT-OSS family models occasionally leak
  `<|channel|>` / `<|json|>` markers into content and tool-call names; we split
  on `<|` defensively.

SSE event types: `start`, `text`, `tool_call`, `tool_result`, `file`,
`delegate_trace`, `plan`, `reflect`, `done`, `error`.

### Sub-agent / delegate

`delegate(task, task_type)` fans out to a specialist via `_run_subagent`. The
orchestrator can call `delegate` multiple times in parallel in a single hop —
each helper runs on the LLM mapped in `SPECIALIST_MODELS`:

| task_type | model |
|-----------|-------|
| vision    | `meta/llama-3.2-90b-vision-instruct` |
| research  | `openai/gpt-oss-120b` |
| document  | `mistralai/mistral-large-3-675b-instruct-2512` |
| code      | `meta/llama-3.3-70b-instruct` |
| reasoning | `openai/gpt-oss-120b` |
| quick     | `openai/gpt-oss-20b` |

Sub-agents have their own 3-hop tool loop and same tool surface. By default
they're `role="leaf"` and **cannot delegate further**, but the orchestrator may
opt a sub-agent into `role="orchestrator"` (via the `role` arg on `delegate`),
which lets that sub-agent fan out to its own leaf workers. Depth is capped at
`MAX_SPAWN_DEPTH = 2` in `chat.py` so recursion can't blow up; orchestrator-
role sub-agents always spawn leaves (no nested orchestrators). Vision sub-
agents get the
uploaded image bytes inlined as data-URL parts; the text-only orchestrator
never sees raw image bytes — it relays the server path in an
`[image attached: …]` note and the user/system prompt instructs it to delegate
to the vision specialist when image inspection is needed. The sub-agent
returns a `_subagent_trace` (text + tool-call records per hop) that the UI
expands inside the delegate chip via the `delegate_trace` SSE event.

## Tool surface (`backend/app/tools_client.py`)

Names match the endpoint path on the tools sidecar (`POST /{name}`).

| Family    | Tools |
|-----------|-------|
| Research  | `web_search` (Tavily), `web_fetch` (HTML→text) |
| Documents | `generate_pdf` (markdown), `generate_xlsx`, `generate_pptx`, `generate_flyer` (single-page poster with hero image) |
| Workspace | `workspace_list/read/write/edit/grep/glob/bash` (per-user-and-project sandbox) |
| Coding    | `codebase_search` (ripgrep-ranked retrieval), `workspace_git_clone` (https-only, SSRF-guarded), `workspace_apply_patch` (`git apply` a unified diff) |
| Sub-agents| `delegate(task, task_type)` |
| Vision    | image upload via `POST /uploads/image` → inlined as base64 data-URL for vision models |

`workspace_*` tools have `user_id` + `workspace_path` injected by the backend —
the LLM never sees them. Path safety in `tools/app/workspace.py:_safe_path`
resolves and verifies that target paths stay inside
`/workspaces/{user_id}/{workspace_slug}`.

## Memory + skills (self-improving loop)

- **Memory** (`backend/app/memory.py`) — when a user starts a new conversation,
  the previous conversation is mined in the background for durable user-level
  facts, which are appended to a per-user memory block injected into every
  future system prompt (`memory.memory_block`).
- **Skills** (`backend/app/skills.py` + `frontend/app/skills`) — user-defined
  prompt fragments with optional `trigger_pattern` regex. On each turn, the
  conversation's attached skills + any auto-triggered skills (regex-matched
  against the first 4 KB of the user message, capped at 250 ms per regex on a
  thread to defuse catastrophic-backtracking patterns) are concatenated into
  the system prompt as `SKILL N (auto-triggered / attached)` blocks.
- **Skills catalog / "Discover"** (`backend/app/catalog.py` + the Discover
  section in `frontend/app/skills/page.tsx`) — a shared, global directory of
  Claude-style SKILL.md files mined from public GitHub, refreshed **daily** by an
  APScheduler cron job (`scheduler.register_catalog_refresh`, default 06:00 UTC,
  cron in `SKILLS_CATALOG_CRON`) plus a boot refresh when the catalog is stale
  (`catalog.refresh_if_stale`). `refresh_catalog()` runs the repo-search queries
  in `SKILLS_CATALOG_QUERIES` (sorted by stars), walks each repo's git tree for
  `SKILL.md`, fetches each from `raw.githubusercontent.com` (no API rate cost),
  parses front-matter via `skills._parse_frontmatter`, and **upserts** into the
  global `catalog_skills` table keyed by `source_url`; rows not re-seen are
  pruned. Works **tokenless** (unauthenticated GitHub API ≈60 req/hr — enough for
  once-daily); `GITHUB_TOKEN` (optional) raises the limit and is sent **only** to
  api.github.com. Routes: `GET /skills/catalog?q=`, `POST
  /skills/catalog/{id}/install` (copies the row into the user's `skills`, deduped
  via `db.find_duplicate_skill`), admin-only `POST /skills/catalog/refresh`.
  Disable with `SKILLS_CATALOG_ENABLED=0`. Fan-out caps:
  `SKILLS_CATALOG_MAX_REPOS` / `_MAX_FILES_PER_REPO` / `_MAX_SKILLS`, plus
  `SKILLS_CATALOG_MAX_PER_REPO` (default 5) — a diversity guard so one mega-repo
  can't flood the catalog.

## Frontend (`/frontend`)

Next.js 15 App Router; pages: `/` (chat), `/login`, `/files`, `/skills`.
Components of note: `Composer`, `MessageList`, `Sidebar`, `ModelPicker`,
`WorkspacePicker`, `SkillsBar`, `ToolChip`, `PlanChip`, `ReflectChip`,
`FileChip`, `FilePanel`, `TipsRotator`, `TomoseMark`. SSE consumption lives in
`lib/sse.ts`; API client in `lib/api.ts`.

### Frontend design constraints

- Aesthetic is "paper-and-ink atelier", **not** a generic AI chat clone.
- Type stack is Fraunces (display) + Instrument Sans (body) + JetBrains Mono.
  **Do not swap to Inter / Geist / Space Grotesk** — that erases the look.
- Single accent: brick (`--color-brick`). Moss + cobalt are reserved for
  file/web semantics. Don't introduce a fourth accent without good reason.
- Tool-call chips are the differentiated surface — keep them characterful, not
  generic loading spinners.

## Environment

Copy `.env.example` → `.env`. Required: `NVIDIA_API_KEY`, `TAVILY_API_KEY`,
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ALLOWED_EMAILS`, `ADMIN_EMAIL`,
`SESSION_SECRET`. Optional: `PLANNER_ENABLED`, `PLANNER_MODEL`,
`WORKSPACES_HOST_DIR`, `OTEL_*` (telemetry no-ops cleanly when unset).

OAuth redirect URI to register in Google Cloud:
`http://localhost:8000/auth/google/callback` (and your prod URL).

## Security notes & non-obvious decisions

- **AI firewall (`guard` sidecar + `backend/app/firewall.py`).** Scans the LLM
  I/O channel, which the sandbox hardening doesn't cover. Input scan (in
  `run_turn`, once per turn before the node loop) hard-**blocks** prompt-injection
  / jailbreak and **deletes the offending user message** so it can't replay into
  the model next turn. Output scan (after the node loop) **redacts** leaked
  secrets and persists + emits the sanitized text. **Hybrid posture**: block in,
  redact out. **Fail-open by default** (`FIREWALL_FAIL_OPEN=1`) — a guard outage
  doesn't take chat down (availability > filter, mirroring how inference fails
  closed but the firewall fails open); flip to fail-closed via env. Honest
  tradeoff: output streams token-by-token, so redaction happens *after* the
  stream — a secret is briefly visible in-flight; the persisted/rendered copy is
  clean. **Pre-stream buffering (phase 3, below) closes this gap when enabled.**
  v1 scanners are Presidio-free (PromptInjection + Toxicity + Regex
  secret-redaction); presidio/spaCy PII redaction and agentic tool-result scanning
  were phase 2 (now shipped, below).
- **AI firewall phase 2 — agentic + PII + code + audit.** Four added controls,
  all through the same `guard` sidecar, posture **defang-not-block**:
  (1) **Tool-result injection scan** — `web_fetch`/`web_search`/`codebase_search`
  results are scanned (`/scan/tool`, reusing the DeBERTa model) for indirect
  prompt injection; on a hit a warning is prepended to what the model reads next
  hop (`firewall_warning` in the tool row → `_to_openai_messages`) + a
  `tool_flagged` SSE event, never a block. (2) **PII redaction** — LLM Guard
  `Sensitive` with a **curated entity set** (`EMAIL_ADDRESS`, `PHONE_NUMBER`,
  `CREDIT_CARD`, `US_SSN`, `IBAN_CODE`, `CRYPTO`, `IP_ADDRESS`; **no
  `PERSON`/`LOCATION`** so normal answers aren't shredded) + a deterministic SSN
  regex backstop. (3) **CodeShield** — `workspace_write`/`workspace_apply_patch`
  code is scanned (`/scan/code`, Meta CodeShield, local Semgrep+regex) and the
  coder is warned to self-correct. (4) **Admin dashboard** — every action logs to
  `firewall_events` (categories/tool/counts/snippet only — **never secret or PII
  values**), surfaced at admin-only `GET /auth/admin/firewall` and in the Sidebar
  admin modal. Flags: `FIREWALL_TOOL_SCAN` / `FIREWALL_PII_OUTPUT` /
  `FIREWALL_CODE_SCAN` (default on). CodeShield's semgrep symlink is created at
  build time so the read-only rootfs doesn't trip; semgrep runs offline with
  metrics off.
- **AI firewall phase 3 — buffering + goal-drift + per-user policy.** Three added
  controls plus an image slim:
  (1) **Pre-stream output buffering** (`FIREWALL_BUFFER_OUTPUT`, default on) — when
  on, `_node_act` withholds the FINAL answer during streaming (`if not buffered`)
  and `run_turn` delivers it as a single `text` SSE only AFTER the output scan, so a
  leaked secret/PII is never visible in-flight. Trade-off: the final answer appears
  all-at-once instead of token-by-token (plan/tool/reflect events still stream live;
  pre-tool *preamble* is still emitted live since it precedes tool results and can't
  carry fetched secrets). (2) **AlignmentCheck / goal-drift** (`FIREWALL_ALIGNMENT_CHECK`,
  default on) — after a tool-using turn, an LLM critic (`_alignment_check`, model
  `ALIGNMENT_MODEL`) audits whether the agent's tool trajectory still serves the
  user's request or was hijacked by injected instructions. **Runs in the backend, not
  the guard sidecar** (it needs the user's OpenRouter creds; guard holds none).
  Posture defang: warn + log + `alignment_flagged` SSE by default; set
  `FIREWALL_ALIGNMENT_BLOCK=1` to replace the answer with a safe stub (most effective
  with buffering on — streamed mode can only swap the already-shown text). Fail-open
  on any critic error. (3) **Per-user firewall policy** — the `firewall_policy` table
  (one nullable column per knob; NULL = inherit the global default) lets an admin set
  posture per user. Resolved once per turn (`db.get_firewall_policy`) and bound via
  `firewall.using_policy()` (a ContextVar, mirroring `nim.using_creds`); `firewall.flag()`
  reads override-else-config. `pii_output` is threaded to the guard `/scan/output` as a
  per-request `pii` field (the sidecar builds the Sensitive scanner in/out accordingly).
  Admin routes `GET /auth/admin/firewall/policies` + `POST /auth/admin/firewall/policy/{uid}`;
  editor in the Sidebar admin modal (click a knob to cycle inherit→on→off). (4) The guard
  image is a **multi-stage build** (builder bakes weights, runtime copies only installed
  packages + `/opt/hf`, dropping bytecode/test trees) — modest win (~6.9→6.65 GB; the
  bulk is torch + model weights + semgrep, which can't be shed). Still later: Cloudflare
  edge firewall; deeper image slimming.
- **Code execution lives only in the tools container.** The backend never
  invokes Bash. Tools is `cap_drop: ALL`, runs as uid 1000 (host-mapped so
  workspace files are user-editable), has no host port, and shell inside it
  uses a stripped env (`SAFE_ENV` in `workspace.py`).
- **The tools image ships a real toolchain** (git, node/npm, gcc/build-essential,
  ripgrep) so the coding agent can clone & build real repos. This means
  LLM-generated code runs with network egress; mitigations: `workspace_git_clone`
  is https-only and runs the host through `web_fetch`'s RFC1918/metadata SSRF
  block, generated code only ever runs under `SAFE_ENV` (no secrets), and the
  container keeps `cap_drop: ALL` + `no-new-privileges`. `BASH_TIMEOUT_MAX` is
  300s (builds/tests) and the backend's sidecar `read` timeout (320s, in
  `tools_client.execute_tool`) is kept above it so long builds don't phantom-time-out.
- **The code specialist is `qwen/qwen3-coder` (480b)** — `SPECIALIST_MODELS["code"]`
  in `chat.py`; `nim._to_nim_id` maps the OpenRouter id to the verified NIM id
  `qwen/qwen3-coder-480b-a35b-instruct`. Code sub-agents get a 12-hop budget.
  NOTE: `codebase_search` is agentic (ripgrep) — there is no embeddings index yet.
  ripgrep MUST be passed an explicit search path under subprocess or it reads stdin.
- **`web_fetch` blocks RFC1918 + localhost** to prevent SSRF against the home LAN.
- **`workspace_edit` requires a unique match** — refuses if `old` occurs 0 or
  >1 times, to avoid silent unintended replacements.
- **Image inlining is base64 data-URL only**, no remote URL pass-through; this
  prevents the model from causing the inference endpoint to fetch internal hosts.
- **`is_admin` is read from email match each login** rather than stored
  mutably, so it tracks `ADMIN_EMAIL` env without manual DB edits.
- **Tool-result summarizer is grounded-only** — its system prompt forbids using
  training-data knowledge; if the payload doesn't contain the answer, it must
  say so. Otherwise hallucinations would launder back into the orchestrator
  context labeled as "tool result".
- **Hallucination critic (`_node_reflect`) deletes the rejected draft from the
  DB** before retrying, so the corrected answer doesn't sit alongside the
  hallucinated one in `list_messages`.
- **DeepSeek R1 / V4 reasoning heads, Kimi-thinking, Magistral** are
  intentionally `supports_tools=False`; they emit reasoning tokens, not
  OpenAI-format tool calls. The dated comments in `_NO_TOOL_PATTERNS` are the
  audit trail — re-probe before flipping any.

## What's deliberately not built (as of v1)

- **MCP server support** — feasible (~200 LOC: spawn stdio MCP servers, wrap
  their tools as `TOOL_DEFINITIONS`). Skipped to keep auth + sandbox audit
  surface small.
- **Per-user model whitelisting / quotas** — single allowlist for now.
- **HTTPS / Caddy reverse proxy in-repo** — TLS termination is handled
  upstream by Cloudflare. `PUBLIC_*_URL` are wired through; `SessionMiddleware`
  flips `https_only` automatically when those URLs are HTTPS.
