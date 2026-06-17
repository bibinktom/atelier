# SCALING.md — Atelier: family app → public SaaS

Status: **planning** (as of 2026-06-10). This document is the roadmap for taking
Atelier from a closed family allowlist (~5 trusted users) to **public use at
100–200 paying customers**. Nothing here is implemented yet; it is the agreed
direction plus the findings of a code audit.

Two decisions are locked:

1. **LLM provisioning = OpenRouter OAuth (PKCE, one-button).** Each user connects
   their own OpenRouter account; the app uses their user-scoped key, so inference
   is **user-funded** (the operator pays $0 for tokens). NIM stays as an optional
   fallback.
2. **Plan for real horizontal scale** — design for N stateless backend replicas
   now (Postgres, Redis, externalized scheduler, object storage), not a single box.

---

## 0. The headline finding

A four-agent audit (concurrency/data, auth/multi-tenancy, tools-sandbox/storage,
SSE/horizontal-scale) agreed on one thing: **the hard, easy-to-get-wrong parts are
already right.**

- Tenant data isolation is uniformly `WHERE user_id = ?` on every query. No IDOR
  found in any live HTTP endpoint.
- Path traversal is properly blocked (`_safe_path` realpath-confinement).
- The sandbox is genuinely well-built: `cap_drop: ALL`, `no-new-privileges`,
  uid 1000, read-only preview mounts, RFC1918/metadata SSRF blocking with
  per-redirect re-validation.
- Sessions are **stateless signed cookies** — the one thing already correct for
  scale-out.

What breaks is **everything that assumes "one trusted instance, a few trusted
users."** Three load-bearing assumptions all fail at 100–200 public customers:

1. **One backend process** (uvicorn, no `--workers`) with **synchronous SQLite on
   the async event loop** → one slow query freezes *every* user's stream.
2. **State pinned to one container** (SQLite file, in-process APScheduler,
   in-memory caches) → a second replica = duplicate cron fires + split-brain DB.
3. **Trust instead of limits** → zero rate limiting, no quotas, no per-user
   resource isolation, shared provider keys, and a 24h "delete everything" purge.

The OpenRouter decision **removes the single scariest item** (the shared inference
bill) because users fund their own inference. It removes **none** of the
infrastructure blockers, and adds one new requirement: **encrypted per-user key
storage**.

> ⚠️ **Fan-out vs. free tier.** OpenRouter free models are rate-limited per user
> (requests/day). This app's fan-out is heavy — one user message can fire
> **15–30 LLM calls** (planner + up to 5 parallel delegates each with their own
> tool loops + per-result summarizers + reflect critic). On a free key that
> ceiling is hit fast, so the fan-out must become **tunable per tier**.

---

## 1. Target architecture (horizontal scale)

```
                 Cloudflare (LB + tunnel)
                          │
            ┌─────────────┼─────────────┐
        backend×N     backend×N      backend×N      ← Gunicorn + uvicorn workers, stateless
            └─────────────┼─────────────┘
        ┌─────────┬───────┼────────┬──────────┐
     Postgres   Redis   Tools-pool  R2/S3   Scheduler (single leader)
   (asyncpg)  (cache/   (per-user   (files/  + Cleanup (single owner)
              ratelimit/ sandboxes, uploads)
              locks/SSE) queued)
```

- **Postgres** (async driver) replaces SQLite — unblocks replicas, ends lock storms.
- **Redis** for shared caches (model catalog, tips), **rate-limit token buckets**,
  per-user concurrency semaphores, distributed locks, and the **SSE resume log**.
- **Backend**: Gunicorn → N uvicorn workers, N replicas, stateless (cookies already
  are). Rolling deploy + graceful drain.
- **Tools**: from one shared box → a **pool with a dispatcher + per-user
  concurrency caps + cgroup/ulimit isolation** (the thorniest piece).
- **Object storage (Cloudflare R2)** for generated files + uploads, per-user quotas.
- **Scheduler + cleanup**: a single leader (or Postgres `SELECT … FOR UPDATE SKIP
  LOCKED`), never per-replica.

---

## 2. OpenRouter OAuth integration (the new feature)

Concrete, ~150 LOC + a settings UI. Flow per the
[OpenRouter PKCE docs](https://openrouter.ai/docs/guides/overview/auth/oauth):

1. **`GET /auth/openrouter/connect`** — generate `code_verifier`, derive
   `code_challenge` (S256), stash the verifier server-side (Redis, keyed to
   session), redirect to
   `https://openrouter.ai/auth?callback_url=<app>&code_challenge=…&code_challenge_method=S256`.
   *(callback_url must be https on port 443/3000 — fine behind Cloudflare.)*
2. **`GET /auth/openrouter/callback?code=…`** —
   `POST https://openrouter.ai/api/v1/auth/keys` with `{code, code_verifier}` →
   returns a **user-controlled key**. Encrypt (Fernet/AES-GCM with a
   `KEY_ENCRYPTION_KEY` env, ideally KMS) and store on the user row.
3. **Provider abstraction** — refactor `backend/app/nim.py` into a provider layer
   that, per request, reads the user's decrypted key and calls
   `https://openrouter.ai/api/v1`. **Route *every* LLM call through the user's
   key** — planner, summarizer, reflect critic, memory extractor, and all
   sub-agents — so inference is fully user-funded. Today those are hardwired to NIM
   models in `config.py`/`chat.py`; map them to OpenRouter IDs (keep NIM optional).
4. **Model catalog** — repoint `config.py:fetch_available_models` at OpenRouter's
   `/models`, surface **`:free` variants** prominently, keep the allowlist +
   capability heuristics.
5. **Key lifecycle** — keys don't expire but users can revoke → a 401 means
   "reconnect"; surface a clean re-auth prompt, not a stack trace.
6. **Monetization** — inference is user-funded, so charge for the *platform*
   (workspaces, tools, docs, scheduling) via Stripe; optionally register as an
   OpenRouter app for referral credit. **Tavily is still the operator's shared
   key** (see P1).

> Why not Ollama (the original idea): Ollama has **no OAuth third-party key
> provisioning** — only manual dashboard keys, or `ollama signin` which registers a
> *local machine's* public key. The one-button flow the operator wanted only exists
> on OpenRouter. Ollama Cloud free tier is real but would require manual key paste.

---

## 3. Prioritized weak-link remediation

Deduped across all four audits. Severity = impact at 100–200 users. Source codes:
`BE` = backend concurrency/data, `AUTH` = auth/multi-tenancy, `TOOLS` =
sandbox/storage, `SSE` = streaming/horizontal-scale.

### 🔴 P0 — Blockers before *any* public traffic

| Fix | Why | Source |
|---|---|---|
| **SQLite → Postgres, async driver, DB calls off the event loop** | Sync `sqlite3` on a single loop + no `busy_timeout` → "database is locked" + frozen SSE for all. Hard blocker to replicas. | BE#1–4, SSE#2 |
| **Per-user rate limits + concurrency caps + usage metering** (Redis token bucket) | Zero rate limiting today. Protects Tavily/tools/disk/DB even with user-funded inference; required for tiers/quotas. | AUTH H3, TOOLS#4/7, SSE#11 |
| **OpenRouter OAuth + route ALL LLM calls through user keys (encrypted at rest)** | The chosen model; resolves shared-inference-bill risk. | §2, AUTH H5 |
| **Self-serve signup** — kill `_MAX_PENDING=50` + manual approval; feature-gate by subscription, not `is_pending` | 51st signup is hard-rejected; attacker fills 50 slots to block real customers; one human can't approve 200 strangers. | AUTH H1, H2, M1 |
| **Replace 24h hard purge** with per-tier retention + user export/delete endpoints | Silently destroys paying customers' chats *and project files* daily; no GDPR export/delete. | AUTH H6, BE#16 |
| **Per-user disk quota** (enforced on write/upload) | One user (or the agent via `workspace_bash`/`curl`) fills the host → fleet-wide opaque `Errno 28` 500s. | AUTH H4, TOOLS#5 |

### 🟠 P1 — Sandbox & resource isolation (the shared tools box is the scariest infra weak link)

| Fix | Why | Source |
|---|---|---|
| **Concurrency semaphores (global + per-user) on tool dispatch**; short tiered timeouts (not 180s) | Sync tool endpoints exhaust FastAPI's ~40-thread pool at ~40 concurrent calls → all tools wedge; saturation back-pressures the backend. | TOOLS#1/7 |
| **Per-command `ulimit`/cgroup (CPU, RSS, nproc) + CPU quota on the tools service; drop/allowlist bash egress** | `workspace_bash` has no concurrency/CPU/pid sub-budget; fork bomb hits shared `pids_limit: 512`; has open internet. | TOOLS#2 |
| **LibreOffice: global concurrency semaphore + persistent `soffice` listener pool** | 4–6 concurrent conversions OOM the 1 GB preview sidecar. | TOOLS#3 |
| **Shared bounded `httpx` client + jittered backoff + token-bucket on Tavily/upstream** | New client per call, no pooling, no global cap → NIM/Tavily 429s; Tavily free tier (~1k/mo) blown in <1 day. | BE#13, TOOLS#4 |
| **Object storage (R2) or high-water eviction + include preview cache in sweep** | Age-only purge runs hourly; backend `/tmp/preview-cache` never purged. | TOOLS#5 |
| **Tune fan-out per tier** (cap delegates/hops on free keys) | 15–30 LLM calls/turn blows free-tier per-user limits. | TOOLS#4 |

### 🟡 P2 — Horizontal scale-out

| Fix | Why | Source |
|---|---|---|
| **Gunicorn + N uvicorn workers, N replicas, rolling deploy + graceful drain** | Single process = CPU-bound serialization ceiling; every `compose up --build` drops all live SSE turns. | SSE#1/6 |
| **Externalize scheduler + cleanup to a single leader** (or PG SKIP LOCKED) | Every replica fires every cron + runs every destructive purge → N× duplicates. | BE#6/7, SSE#3/13 |
| **Redis-backed shared caches; async refreshable model catalog** | `AVAILABLE_MODELS` fetched once at import (blocking), diverges per worker; tips cache stampedes. | BE#5/10, SSE#4 |
| **Migrations as a one-shot job (Alembic), gated before replicas boot** | `init_db()` runs DDL + `PRAGMA writable_schema` on every boot → concurrent-boot corruption. | SSE#10 |

### 🟢 P3 — SSE robustness

| Fix | Why | Source |
|---|---|---|
| **Client-disconnect detection** (`request.is_disconnected()` between hops) → abort turn | Abandoned turns keep burning the user's OpenRouter key + hold the loop. | SSE#5 |
| **SSE resume** (event IDs + buffered replay, or durable per-turn event log + background execution) | No reconnect today; a tunnel blip (~100s idle) loses the turn; on multi-replica a reconnect lands on the wrong replica. | SSE#7 |

### 🔵 P4 — Security & compliance hardening

- Session rotation on login + shorter max-age + prod CORS/SameSite (AUTH M3, SSE#12)
- Upload magic-byte sniffing + forced-attachment/CSP (AUTH M4/M5)
- Message-length + conversation-size caps (AUTH M6)
- Account revocation endpoint (AUTH M2)
- Push `user_id` into `delete_message`/`list_messages`/task helpers for
  defense-in-depth (AUTH C1/C2 — latent IDOR if reused)
- Gate `workspace_bash`/`schedule_create` behind opt-in + treat `web_fetch` output
  as untrusted (AUTH M7)
- Private ntfy topic + `SESSION_SECRET` rotation plan (AUTH L1/L2)

### ⚪ P5 — Observability (do alongside P2)

Real **readiness** probe (DB + OpenRouter + tools), `/metrics` (active-SSE gauge,
LLM latency/error counters), structured JSON logs + request IDs (replace `print()`),
Sentry, Compose healthchecks + alerting. Today `/healthz` always returns 200 even
when the DB is locked — a load balancer would route to a dead backend (SSE#9).

---

## 4. Suggested sequencing (milestones)

- **M1 — "Safe to charge money" (P0):** Postgres + OpenRouter OAuth + rate
  limits/metering + real retention + disk quotas + self-serve signup. Makes it a
  *correct* (if still single-instance) product.
- **M2 — "Won't fall over" (P1 + P5):** sandbox isolation + upstream backoff +
  object storage + observability.
- **M3 — "Scales out" (P2 + P3):** workers/replicas, externalized scheduler, SSE
  resume.
- **M4 — "Hardened" (P4):** the security follow-on pass.

You *could* serve 100–200 users on one well-tuned box after M1+M2 (Postgres +
isolation + limits go a long way) and defer full replica work until needed — but
building Postgres/Redis/stateless from the start (M1) means no rewrites when you
flip on replicas.

**Recommended first piece:** OpenRouter OAuth (smallest, highest-leverage) +
Postgres migration (everything depends on it).

---

## 5. Audit severity reference

Full per-finding detail lives in the conversation that produced this doc. Compact
rollup:

| Area | CRITICAL | HIGH | Notable MEDIUM/LOW |
|---|---|---|---|
| Concurrency / data (BE) | sync SQLite on event loop; no `busy_timeout`; conn-per-call; single-file serialization | model catalog fetched-once/blocking; in-proc scheduler dup fires; blocking image base64; O(N) dedup scans | racy tips cache; bg memory extraction TOCTOU; per-call httpx clients; FTS orphan rows |
| Auth / multi-tenancy (AUTH) | — | no self-serve signup; `_MAX_PENDING=50` lockout; **no rate limiting**; no disk quota; shared keys; 24h purge | open OAuth gate; no revocation; 30-day session no rotation; client-MIME trust; no msg-length cap; injection surface |
| Tools / storage (TOOLS) | shared sandbox no isolation; `workspace_bash` no caps; LibreOffice OOM | shared Tavily/NIM keys no backoff; storage growth → `Errno 28`; no backend↔tools backpressure | web_fetch DNS-rebind TOCTOU + proxy abuse |
| SSE / horizontal (SSE) | uvicorn single-process; local SQLite; in-proc scheduler | no disconnect handling; no graceful drain; no SSE resume; observability gaps; no rate limit | startup migrations race; single-origin CORS; per-replica cleanup |

**One genuinely correct-for-scale decision already in place:** stateless
signed-cookie sessions. **The genuinely fatal ones:** SQLite + single uvicorn
process + per-replica scheduler.

---

*Generated from a 4-agent scaling audit on 2026-06-10. Keep this in sync with
`CLAUDE.md` (which still describes the closed family app) as implementation
proceeds.*
