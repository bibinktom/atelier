import json
import os
import re
import sqlite3
import time
import unicodedata
import uuid
from contextlib import contextmanager
from typing import Iterator

from . import config
from .config import DB_PATH


# ---------- text normalization for dedup ----------

_STOPWORDS = {
    "the", "a", "an", "of", "to", "for", "is", "are", "and", "or", "with",
    "in", "on", "at", "by", "this", "that", "user", "be", "has", "have", "had",
    "i", "you", "they", "their", "his", "her", "its", "as", "from",
}
_CURRENCY = re.compile(r"[₹$€£¥₩₽¢]")
_DIGIT_SHORT = re.compile(r"(\d+(?:\.\d+)?)\s*([kKmMbB])\b")
_PUNCT = re.compile(r"[^\w\s]")
_MULTI_WS = re.compile(r"\s+")


def _expand_short(m: re.Match) -> str:
    n = float(m.group(1))
    mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[m.group(2).lower()]
    return str(int(n * mult))


def normalize_text(s: str) -> str:
    """Lowercase, strip currency/punctuation, expand 40k→40000, drop stopwords.

    Used so memories/skill names that differ only in formatting or filler words
    collapse to the same key. Word order is preserved to avoid conflating
    sentences with different subject/object roles.
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower()
    s = _CURRENCY.sub("", s)
    s = _DIGIT_SHORT.sub(_expand_short, s)
    s = s.replace(",", "")  # digit separators
    s = _PUNCT.sub(" ", s)
    s = _MULTI_WS.sub(" ", s).strip()
    tokens = [t for t in s.split() if t not in _STOPWORDS]
    return " ".join(tokens)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL COLLATE NOCASE,
    name TEXT,
    picture TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    is_pending INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    -- Per-user OpenRouter API key, Fernet-encrypted (see crypto.py). NULL = not connected.
    openrouter_key_enc TEXT,
    openrouter_connected_at INTEGER
);

-- Permanent record of admin approval. Survives users-row deletion or DB
-- rebuilds: any email here is auto-approved on next OAuth login.
-- approved_by is the admin user_id at the time of approval; intentionally
-- NOT a FK so an email stays approved even if the admin record is later
-- removed (which is exactly the durability guarantee this table provides).
CREATE TABLE IF NOT EXISTS approved_emails (
    email TEXT PRIMARY KEY COLLATE NOCASE,
    approved_at INTEGER NOT NULL,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, slug)
);
CREATE INDEX IF NOT EXISTS idx_workspaces_user ON workspaces(user_id, created_at);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT 'New chat',
    model TEXT NOT NULL,
    workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,           -- 'user' | 'assistant' | 'tool' | 'system'
    content TEXT NOT NULL,        -- JSON: either a string or structured (tool calls etc.)
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    path TEXT NOT NULL,
    mime TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user ON files(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                                       -- 'fact' | 'preference' | 'lesson'
    content TEXT NOT NULL,
    importance INTEGER NOT NULL DEFAULT 5,
    created_at INTEGER NOT NULL,
    source_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, importance DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    prompt_template TEXT NOT NULL,
    body_md TEXT,                                            -- Claude-style instructions injected into system prompt
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at INTEGER,
    is_suggested INTEGER NOT NULL DEFAULT 0,                 -- 1 = AI-suggested but not accepted
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_skills_user ON skills(user_id, is_suggested, use_count DESC);

-- Shared, global catalog of skills discovered on GitHub by the daily refresh job
-- (backend/app/catalog.py). Not owned by any user; users *install* a row, which
-- copies it into their own `skills` table. Keyed by source_url (the SKILL.md
-- permalink) so a re-refresh upserts instead of duplicating.
CREATE TABLE IF NOT EXISTS catalog_skills (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'github',
    source_url TEXT NOT NULL UNIQUE,                         -- canonical html_url of the SKILL.md
    repo TEXT,                                               -- "owner/name"
    repo_url TEXT,
    author TEXT,
    name TEXT NOT NULL,
    description TEXT,
    body_md TEXT,                                            -- the SKILL.md body (Claude-style instructions)
    prompt_template TEXT,                                    -- derived trigger prompt
    stars INTEGER NOT NULL DEFAULT 0,                        -- repo stargazers (ranking signal)
    license TEXT,
    content_hash TEXT,                                       -- to detect unchanged files cheaply
    install_count INTEGER NOT NULL DEFAULT 0,
    fetched_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_rank ON catalog_skills(stars DESC, install_count DESC);

-- FTS5 for cross-conversation message search.
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    message_id    UNINDEXED,
    conversation_id UNINDEXED,
    user_id       UNINDEXED,
    role          UNINDEXED,
    created_at    UNINDEXED,
    tokenize='porter unicode61'
);
"""


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with connect() as c:
        c.executescript(SCHEMA)
        # Idempotent migrations for older DBs
        cols = {r["name"] for r in c.execute("PRAGMA table_info(conversations)").fetchall()}
        if "workspace_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL")
        if "last_extracted_at" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN last_extracted_at INTEGER NOT NULL DEFAULT 0")
        if "skill_id" not in cols:
            c.execute("ALTER TABLE conversations ADD COLUMN skill_id TEXT REFERENCES skills(id) ON DELETE SET NULL")
        skill_cols = {r["name"] for r in c.execute("PRAGMA table_info(skills)").fetchall()}
        if "body_md" not in skill_cols:
            c.execute("ALTER TABLE skills ADD COLUMN body_md TEXT")
        if "trigger_pattern" not in skill_cols:
            # Auto-trigger: when a user message matches this regex, inject the skill's
            # body_md into system_prompt for that turn (without persistent attach).
            c.execute("ALTER TABLE skills ADD COLUMN trigger_pattern TEXT")
        # Skill chaining: many-to-many between conversations and skills (additive on top of
        # the existing single skill_id column, which we keep as the "primary" skill).
        c.executescript("""
            CREATE TABLE IF NOT EXISTS conversation_skills (
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                attached_at INTEGER NOT NULL,
                PRIMARY KEY (conversation_id, skill_id)
            );
            CREATE INDEX IF NOT EXISTS idx_conv_skills_conv ON conversation_skills(conversation_id);

            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'pending',  -- pending | in_progress | completed | cancelled
                output TEXT,                              -- progress notes appended via task_output
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                completed_at INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_conv ON tasks(conversation_id, created_at);

            CREATE TABLE IF NOT EXISTS schedules (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                cron_expr TEXT NOT NULL,            -- 5-field cron: 'M H DOM MON DOW'
                prompt_text TEXT NOT NULL,
                model TEXT,                          -- if NULL, falls back to user's default
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                last_run_at INTEGER,
                last_conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_schedules_user ON schedules(user_id, enabled);

            -- Append-only audit log. No FK constraints on purpose: recording a
            -- firewall action must never fail (or be erased) because a user/
            -- conversation row is missing or later deleted.
            CREATE TABLE IF NOT EXISTS firewall_events (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                conversation_id TEXT,
                phase TEXT NOT NULL,    -- input | output | tool | code | alignment
                status TEXT NOT NULL,   -- blocked | redacted | flagged
                detail TEXT,            -- JSON: flagged categories / tool / counts / snippet (NEVER secret values)
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_firewall_created ON firewall_events(created_at);

            -- Per-user firewall policy overrides (phase 3). One row per user who has
            -- a non-default posture; a NULL column means "inherit the global config
            -- default". Admin-managed. Keyed by user_id (no FK: a missing user just
            -- means the row is dormant, and policy lookups tolerate absence).
            CREATE TABLE IF NOT EXISTS firewall_policy (
                user_id TEXT PRIMARY KEY,
                fail_open INTEGER,        -- NULL=inherit, 0/1=override
                tool_scan INTEGER,
                code_scan INTEGER,
                pii_output INTEGER,
                buffer_output INTEGER,
                alignment_check INTEGER,
                alignment_block INTEGER,
                updated_at INTEGER NOT NULL
            );
        """)
        # Local desktop build: "always allow" rules for the action-permission gate.
        c.execute("""
            CREATE TABLE IF NOT EXISTS permission_rules (
                user_id TEXT NOT NULL,
                rule_key TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (user_id, rule_key)
            );
        """)

        # Local desktop build: a workspace can map to an absolute host folder the
        # user picked (host_path) instead of the container's /workspaces/<user>/<slug>.
        ws_cols = {r["name"] for r in c.execute("PRAGMA table_info(workspaces)").fetchall()}
        if "host_path" not in ws_cols:
            c.execute("ALTER TABLE workspaces ADD COLUMN host_path TEXT")

        # Memory upgrades: categories, promotion bookkeeping, episodic scoping.
        mem_cols = {r["name"] for r in c.execute("PRAGMA table_info(memories)").fetchall()}
        if "category" not in mem_cols:
            c.execute("ALTER TABLE memories ADD COLUMN category TEXT")
        if "use_count" not in mem_cols:
            c.execute("ALTER TABLE memories ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0")
        if "last_used_at" not in mem_cols:
            c.execute("ALTER TABLE memories ADD COLUMN last_used_at INTEGER")
        if "conversation_id" not in mem_cols:
            # When set, the memory is episodic — only injected into that conversation.
            c.execute("ALTER TABLE memories ADD COLUMN conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL")
        # Open-signup with admin approval: new users land in pending state until
        # the admin approves them. Existing users (created before this column)
        # default to 0 = approved so we don't lock anyone out.
        user_cols = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "is_pending" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN is_pending INTEGER NOT NULL DEFAULT 0")
        # Single-use approval/deny tokens carried inside ntfy push notifications.
        # The admin clicks Approve/Deny on their phone; the action server matches
        # the token to a pending user and flips state without needing a session.
        if "approval_token" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN approval_token TEXT")
        if "approval_token_expires_at" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN approval_token_expires_at INTEGER")

        # Case-insensitive email lookups. Older DBs were created without
        # COLLATE NOCASE on users.email — if Google ever returns the email
        # with different casing, the row lookup misses and the user falls
        # back to the pending queue with a duplicate row. Rebuild the table
        # (id values preserved, so foreign keys stay valid) when needed.
        sql_row = c.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        existing_users_sql = (sql_row["sql"] if sql_row else "") or ""
        if "COLLATE NOCASE" not in existing_users_sql.upper():
            # Match the canonical schema columns. We list them explicitly so
            # the rebuild produces a row layout identical to a fresh DB.
            c.executescript("""
                BEGIN;
                ALTER TABLE users RENAME TO users_old;
                CREATE TABLE users (
                    id TEXT PRIMARY KEY,
                    email TEXT UNIQUE NOT NULL COLLATE NOCASE,
                    name TEXT,
                    picture TEXT,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    is_pending INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL
                );
                INSERT INTO users(id, email, name, picture, is_admin, is_pending, created_at)
                SELECT id, LOWER(TRIM(email)), name, picture, is_admin, is_pending, created_at
                FROM users_old;
                DROP TABLE users_old;
                COMMIT;
            """)

        # SQLite's ALTER TABLE RENAME also rewrites every FOREIGN KEY clause
        # that referenced the renamed table — so the NOCASE rebuild above
        # silently re-pointed conversations/files/workspaces/memories/skills/
        # tasks/schedules/approved_emails FKs to "users_old". Once we DROP'd
        # users_old, every one of those FKs is dangling, and the next FK
        # check raises 'no such table: main.users_old'. Heal the schema in
        # place via the documented writable_schema escape hatch — this only
        # rewrites the stored CREATE TABLE text, not row data.
        if c.execute(
            "SELECT 1 FROM sqlite_master WHERE sql LIKE '%users_old%' LIMIT 1"
        ).fetchone():
            c.execute("PRAGMA writable_schema = 1")
            c.execute(
                "UPDATE sqlite_master SET sql = REPLACE(sql, 'users_old', 'users') "
                "WHERE sql LIKE '%users_old%'"
            )
            c.execute("PRAGMA writable_schema = 0")
            # Bump schema_version so the in-process schema cache reloads.
            current_ver = c.execute("PRAGMA schema_version").fetchone()[0]
            c.execute(f"PRAGMA schema_version = {current_ver + 1}")

        # Backfill the persistent approval allowlist from already-approved
        # users. INSERT OR IGNORE so this is idempotent across boots.
        c.execute(
            "INSERT OR IGNORE INTO approved_emails(email, approved_at, approved_by) "
            "SELECT email, COALESCE(created_at, ?), id FROM users WHERE is_pending = 0",
            (now(),),
        )

        # Per-user OpenRouter key columns. Added AFTER the COLLATE rebuild above so
        # the rebuild (which copies a fixed column list) can't drop them. Re-read
        # table_info because the rebuild may have replaced the table since line ~225.
        user_cols2 = {r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()}
        if "openrouter_key_enc" not in user_cols2:
            c.execute("ALTER TABLE users ADD COLUMN openrouter_key_enc TEXT")
        if "openrouter_connected_at" not in user_cols2:
            c.execute("ALTER TABLE users ADD COLUMN openrouter_connected_at INTEGER")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
    finally:
        conn.close()


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex


# ---- users ----

def upsert_user(email: str, name: str, picture: str, is_admin: bool,
                is_pending: bool = False) -> dict:
    """Insert a new user (with the supplied is_pending state) or update an
    existing one. Pending state is NEVER changed for existing users — only the
    admin can flip is_pending=0 via approve_user.
    """
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            c.execute(
                "UPDATE users SET name = ?, picture = ?, is_admin = ? WHERE id = ?",
                (name, picture, int(is_admin), row["id"]),
            )
            return dict(row) | {"name": name, "picture": picture, "is_admin": int(is_admin)}
        uid = new_id()
        c.execute(
            "INSERT INTO users(id, email, name, picture, is_admin, is_pending, created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (uid, email, name, picture, int(is_admin), int(is_pending), now()),
        )
        return {
            "id": uid, "email": email, "name": name, "picture": picture,
            "is_admin": int(is_admin), "is_pending": int(is_pending),
            "created_at": now(),
        }


def get_user(user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


# ---- per-user OpenRouter key ----

def set_openrouter_key(user_id: str, key_enc: str) -> None:
    """Store the Fernet-encrypted OpenRouter key for a user and stamp connect time."""
    with connect() as c:
        c.execute(
            "UPDATE users SET openrouter_key_enc = ?, openrouter_connected_at = ? WHERE id = ?",
            (key_enc, now(), user_id),
        )


def get_openrouter_key_enc(user_id: str) -> str | None:
    """Return the encrypted OpenRouter key for a user (or None if not connected).
    Fetched only on the LLM path — never surfaced to the frontend."""
    with connect() as c:
        row = c.execute(
            "SELECT openrouter_key_enc FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return (row["openrouter_key_enc"] if row else None) or None


def clear_openrouter_key(user_id: str) -> None:
    with connect() as c:
        c.execute(
            "UPDATE users SET openrouter_key_enc = NULL, openrouter_connected_at = NULL WHERE id = ?",
            (user_id,),
        )


def count_pending_users() -> int:
    with connect() as c:
        row = c.execute("SELECT COUNT(*) AS n FROM users WHERE is_pending = 1").fetchone()
        return int(row["n"]) if row else 0


def set_approval_token(user_id: str, token: str, *, ttl_seconds: int = 7 * 86400) -> None:
    """Stamp a single-use approval token on a pending user. The token is only
    a valid identifier for THIS user — leaking it lets an attacker approve
    one specific pending account, nothing else."""
    expires = now() + ttl_seconds
    with connect() as c:
        c.execute(
            "UPDATE users SET approval_token = ?, approval_token_expires_at = ? WHERE id = ?",
            (token, expires, user_id),
        )


def get_user_by_approval_token(token: str) -> dict | None:
    if not token:
        return None
    with connect() as c:
        row = c.execute(
            "SELECT * FROM users WHERE approval_token = ? AND approval_token_expires_at > ?",
            (token, now()),
        ).fetchone()
        return dict(row) if row else None


def clear_approval_token(user_id: str) -> None:
    with connect() as c:
        c.execute(
            "UPDATE users SET approval_token = NULL, approval_token_expires_at = NULL WHERE id = ?",
            (user_id,),
        )


def add_approved_email(email: str, approved_by: str | None = None) -> None:
    """Mark an email as permanently approved. Idempotent — safe to call on
    already-approved addresses. The email column uses COLLATE NOCASE so case
    variants collapse to a single row."""
    if not email:
        return
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO approved_emails(email, approved_at, approved_by) "
            "VALUES(?, ?, ?)",
            (email.strip(), now(), approved_by),
        )


def is_email_approved(email: str) -> bool:
    if not email:
        return False
    with connect() as c:
        row = c.execute(
            "SELECT 1 FROM approved_emails WHERE email = ? LIMIT 1",
            (email.strip(),),
        ).fetchone()
        return row is not None


def list_pending_users() -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT id, email, name, picture, created_at FROM users "
            "WHERE is_pending = 1 ORDER BY created_at DESC",
        ).fetchall()
        return [dict(r) for r in rows]


def approve_user(user_id: str) -> bool:
    with connect() as c:
        cur = c.execute("UPDATE users SET is_pending = 0 WHERE id = ?", (user_id,))
        return (cur.rowcount or 0) > 0


def delete_user(user_id: str) -> bool:
    """Hard-delete a user and (via FK cascade) all their data."""
    with connect() as c:
        cur = c.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return (cur.rowcount or 0) > 0


# ---- workspaces ----

def list_workspaces(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM workspaces WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_workspace(workspace_id: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM workspaces WHERE id = ? AND user_id = ?", (workspace_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def get_workspace_by_slug(user_id: str, slug: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM workspaces WHERE user_id = ? AND slug = ?", (user_id, slug),
        ).fetchone()
        return dict(row) if row else None


def create_workspace(user_id: str, name: str, slug: str, host_path: str | None = None) -> dict:
    wid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO workspaces(id, user_id, name, slug, host_path, created_at) VALUES(?,?,?,?,?,?)",
            (wid, user_id, name, slug, host_path, t),
        )
    return {"id": wid, "user_id": user_id, "name": name, "slug": slug,
            "host_path": host_path, "created_at": t}


def delete_workspace(workspace_id: str, user_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM workspaces WHERE id = ? AND user_id = ?", (workspace_id, user_id))


def ensure_default_workspace(user_id: str) -> dict:
    """Return user's first workspace, creating one if none exist. In the local
    desktop build the default maps to the configured host root (e.g. ~) so the
    agent has a real folder to work in out of the box."""
    existing = list_workspaces(user_id)
    if existing:
        return existing[0]
    if config.ATELIER_LOCAL:
        return create_workspace(user_id, name=os.path.basename(config.ATELIER_LOCAL_ROOT) or "Home",
                                slug="home", host_path=config.ATELIER_LOCAL_ROOT)
    return create_workspace(user_id, name="General", slug="general")


# ---- conversations ----

def create_conversation(user_id: str, model: str, title: str = "New chat",
                        workspace_id: str | None = None, skill_id: str | None = None) -> dict:
    cid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO conversations(id, user_id, title, model, workspace_id, skill_id, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (cid, user_id, title, model, workspace_id, skill_id, t, t),
        )
    return {"id": cid, "user_id": user_id, "title": title, "model": model,
            "workspace_id": workspace_id, "skill_id": skill_id, "created_at": t, "updated_at": t}


def list_conversations(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_conversation(cid: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id),
        ).fetchone()
        return dict(row) if row else None


def update_conversation(cid: str, *, title: str | None = None, model: str | None = None,
                        workspace_id: str | None = None, _clear_workspace: bool = False,
                        skill_id: str | None = None, _clear_skill: bool = False) -> None:
    sets, vals = [], []
    if title is not None:
        sets.append("title = ?"); vals.append(title)
    if model is not None:
        sets.append("model = ?"); vals.append(model)
    if _clear_workspace:
        sets.append("workspace_id = NULL")
    elif workspace_id is not None:
        sets.append("workspace_id = ?"); vals.append(workspace_id)
    if _clear_skill:
        sets.append("skill_id = NULL")
    elif skill_id is not None:
        sets.append("skill_id = ?"); vals.append(skill_id)
    sets.append("updated_at = ?"); vals.append(now())
    vals.append(cid)
    with connect() as c:
        c.execute(f"UPDATE conversations SET {', '.join(sets)} WHERE id = ?", vals)


def delete_conversation(cid: str, user_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM conversations WHERE id = ? AND user_id = ?", (cid, user_id))


# ---- messages ----

def _fts_text(role: str, content) -> str | None:
    """Pick a searchable text snippet for FTS. Skip noise (tool calls, tool results)."""
    if role == "user":
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return content.get("text") or None
    elif role == "assistant":
        if isinstance(content, str):
            return content or None
        if isinstance(content, dict):
            return content.get("content") or None
    return None


def add_message(conversation_id: str, role: str, content) -> dict:
    mid = new_id()
    payload = json.dumps(content) if not isinstance(content, str) else content
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES(?,?,?,?,?)",
            (mid, conversation_id, role, payload, t),
        )
        c.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (t, conversation_id))
        # Populate FTS for searchable roles.
        text = _fts_text(role, content)
        if text:
            user_row = c.execute(
                "SELECT user_id FROM conversations WHERE id = ?", (conversation_id,),
            ).fetchone()
            if user_row:
                c.execute(
                    "INSERT INTO messages_fts(content, message_id, conversation_id, user_id, role, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (text, mid, conversation_id, user_row["user_id"], role, t),
                )
    return {"id": mid, "conversation_id": conversation_id, "role": role, "content": payload, "created_at": t}


def delete_message(message_id: str, conversation_id: str) -> None:
    """Hard-delete a single message. Used by the reflect re-loop to remove a
    rejected draft before the orchestrator emits its corrected version, so we
    don't end up with two final answers in the conversation history."""
    with connect() as c:
        c.execute(
            "DELETE FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        )
        c.execute("DELETE FROM messages_fts WHERE message_id = ?", (message_id,))


def update_message_text(message_id: str, conversation_id: str, new_text: str) -> None:
    """Replace the text of an existing message in place. Used by the AI firewall to
    persist a redacted final answer over the streamed original. Handles both the
    plain-string and the {content, plan, ...} dict content shapes, and rebuilds the
    FTS row so search reflects the redaction."""
    with connect() as c:
        row = c.execute(
            "SELECT content, role FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if not row:
            return
        try:
            parsed = json.loads(row["content"])
        except (json.JSONDecodeError, TypeError):
            parsed = row["content"]
        if isinstance(parsed, dict):
            parsed["content"] = new_text
            payload = json.dumps(parsed)
        else:
            payload = new_text
        c.execute(
            "UPDATE messages SET content = ? WHERE id = ? AND conversation_id = ?",
            (payload, message_id, conversation_id),
        )
        c.execute("DELETE FROM messages_fts WHERE message_id = ?", (message_id,))
        text = _fts_text(row["role"], new_text)
        if text:
            user_row = c.execute(
                "SELECT user_id FROM conversations WHERE id = ?", (conversation_id,),
            ).fetchone()
            if user_row:
                c.execute(
                    "INSERT INTO messages_fts(content, message_id, conversation_id, user_id, role, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (text, message_id, conversation_id, user_row["user_id"], row["role"], now()),
                )


def log_firewall_event(user_id: str | None, conversation_id: str | None,
                       phase: str, status: str, detail: dict | None = None) -> None:
    """Record one firewall action for the admin dashboard. `detail` must contain
    ONLY non-sensitive metadata (flagged categories, tool name, counts, a short
    snippet) — never the secret/PII value itself."""
    try:
        with connect() as c:
            c.execute(
                "INSERT INTO firewall_events(id, user_id, conversation_id, phase, status, detail, created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (new_id(), user_id, conversation_id, phase, status,
                 json.dumps(detail or {}), now()),
            )
    except Exception as e:  # noqa: BLE001 — logging must never break a chat turn
        print(f"[firewall] failed to log event: {type(e).__name__}: {e}")


def list_firewall_events(limit: int = 100, user_id: str | None = None) -> list[dict]:
    with connect() as c:
        if user_id:
            rows = c.execute(
                "SELECT * FROM firewall_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM firewall_events ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["detail"] = json.loads(d.get("detail") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["detail"] = {}
        out.append(d)
    return out


def firewall_event_counts() -> dict:
    """Aggregate counts by phase and by status for the dashboard header."""
    with connect() as c:
        by_phase = {r["phase"]: r["n"] for r in c.execute(
            "SELECT phase, COUNT(*) AS n FROM firewall_events GROUP BY phase").fetchall()}
        by_status = {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) AS n FROM firewall_events GROUP BY status").fetchall()}
        total = c.execute("SELECT COUNT(*) AS n FROM firewall_events").fetchone()["n"]
    return {"total": total, "by_phase": by_phase, "by_status": by_status}


# ---- per-user firewall policy ----

_POLICY_COLS = ("fail_open", "tool_scan", "code_scan", "pii_output",
                "buffer_output", "alignment_check", "alignment_block")


def get_firewall_policy(user_id: str) -> dict | None:
    """Return {col: 0|1|None} for a user, or None if they have no override row.
    A None value on a column means 'inherit the global default'."""
    if not user_id:
        return None
    with connect() as c:
        row = c.execute("SELECT * FROM firewall_policy WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    return {k: row[k] for k in _POLICY_COLS}


def set_firewall_policy(user_id: str, patch: dict) -> dict:
    """Upsert a user's policy overrides. Each key in `patch` may be 0, 1, or None
    (None clears the override → inherit). Unknown keys are ignored."""
    clean = {k: (None if patch[k] is None else int(bool(patch[k])))
             for k in _POLICY_COLS if k in patch}
    with connect() as c:
        existing = c.execute("SELECT user_id FROM firewall_policy WHERE user_id = ?",
                             (user_id,)).fetchone()
        if existing:
            if clean:
                sets = ", ".join(f"{k} = ?" for k in clean)
                c.execute(f"UPDATE firewall_policy SET {sets}, updated_at = ? WHERE user_id = ?",
                          (*clean.values(), now(), user_id))
            else:
                c.execute("UPDATE firewall_policy SET updated_at = ? WHERE user_id = ?",
                          (now(), user_id))
        else:
            cols = ["user_id", *clean.keys(), "updated_at"]
            vals = [user_id, *clean.values(), now()]
            c.execute(f"INSERT INTO firewall_policy({', '.join(cols)}) "
                      f"VALUES({', '.join('?' for _ in cols)})", vals)
    return get_firewall_policy(user_id) or {}


def list_firewall_policies() -> dict[str, dict]:
    """All override rows, keyed by user_id (for the admin editor)."""
    with connect() as c:
        rows = c.execute("SELECT * FROM firewall_policy").fetchall()
    return {r["user_id"]: {k: r[k] for k in _POLICY_COLS} for r in rows}


def is_permission_allowed(user_id: str, rule_key: str) -> bool:
    with connect() as c:
        return c.execute("SELECT 1 FROM permission_rules WHERE user_id = ? AND rule_key = ?",
                         (user_id, rule_key)).fetchone() is not None


def add_permission_rule(user_id: str, rule_key: str) -> None:
    with connect() as c:
        c.execute("INSERT OR IGNORE INTO permission_rules(user_id, rule_key, created_at) "
                  "VALUES(?,?,?)", (user_id, rule_key, now()))


def list_permission_rules(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT rule_key, created_at FROM permission_rules WHERE user_id = ? "
                         "ORDER BY created_at DESC", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def delete_permission_rule(user_id: str, rule_key: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM permission_rules WHERE user_id = ? AND rule_key = ?",
                  (user_id, rule_key))


def list_users() -> list[dict]:
    """Approved (non-pending) users, for the admin policy editor."""
    with connect() as c:
        rows = c.execute(
            "SELECT id, email, name, picture FROM users WHERE is_pending = 0 "
            "ORDER BY email").fetchall()
    return [dict(r) for r in rows]


def list_messages(conversation_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["content"] = json.loads(d["content"])
            except (ValueError, TypeError):
                pass
            out.append(d)
        return out


# ---- files ----

def add_file(user_id: str, conversation_id: str | None, filename: str, path: str, mime: str, size: int) -> dict:
    fid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO files(id, user_id, conversation_id, filename, path, mime, size, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (fid, user_id, conversation_id, filename, path, mime, size, t),
        )
    return {"id": fid, "filename": filename, "mime": mime, "size": size, "created_at": t}


def get_file(file_id: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM files WHERE id = ? AND user_id = ?", (file_id, user_id),
        ).fetchone()
        return dict(row) if row else None


# ---- memories ----

def list_memories(user_id: str, *, limit: int = 200, category: str | None = None) -> list[dict]:
    with connect() as c:
        if category:
            rows = c.execute(
                "SELECT * FROM memories WHERE user_id = ? AND category = ? "
                "ORDER BY importance DESC, created_at DESC LIMIT ?",
                (user_id, category, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM memories WHERE user_id = ? "
                "ORDER BY importance DESC, created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


def mark_memories_used(memory_ids: list[str]) -> None:
    """Bump use_count + last_used_at — promotion signal for memories that were injected."""
    if not memory_ids:
        return
    t = now()
    with connect() as c:
        c.executemany(
            "UPDATE memories SET use_count = use_count + 1, last_used_at = ? WHERE id = ?",
            [(t, mid) for mid in memory_ids],
        )


def decay_memories(user_id: str, *, max_keep: int = 200,
                   stale_after_seconds: int = 60 * 60 * 24 * 30) -> int:
    """Cheap memory hygiene: drop importance for never-used stale memories,
    delete anything that's both bottom-importance and very stale, cap total to max_keep.

    Returns number of memories deleted.
    """
    deleted = 0
    cutoff = now() - stale_after_seconds
    with connect() as c:
        # 1. Decrement importance on stale, never-referenced memories.
        c.execute(
            "UPDATE memories SET importance = MAX(1, importance - 1) "
            "WHERE user_id = ? AND use_count = 0 "
            "AND created_at < ? AND (last_used_at IS NULL OR last_used_at < ?)",
            (user_id, cutoff, cutoff),
        )
        # 2. Hard-delete anything bottom-importance + very stale.
        cur = c.execute(
            "DELETE FROM memories WHERE user_id = ? AND importance <= 1 "
            "AND use_count = 0 AND created_at < ? "
            "AND (last_used_at IS NULL OR last_used_at < ?)",
            (user_id, cutoff, cutoff),
        )
        deleted += cur.rowcount or 0
        # 3. Cap total — keep top max_keep by composite score.
        rows = c.execute(
            "SELECT id FROM memories WHERE user_id = ? "
            "ORDER BY (importance + MIN(COALESCE(use_count, 0), 5)) DESC, "
            "         COALESCE(last_used_at, created_at) DESC",
            (user_id,),
        ).fetchall()
        if len(rows) > max_keep:
            to_drop = [r["id"] for r in rows[max_keep:]]
            c.executemany("DELETE FROM memories WHERE id = ?", [(i,) for i in to_drop])
            deleted += len(to_drop)
    return deleted


def top_memories(user_id: str, *, limit: int = 12,
                 conversation_id: str | None = None) -> list[dict]:
    """Top-N memories for system-prompt injection.

    Pulls the lifetime store (conversation_id IS NULL) plus any episodic memories
    bound to the current conversation. Ranking blends importance with use_count so
    memories that get cited regularly stay near the top (promotion).
    """
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM memories WHERE user_id = ? "
            "AND (conversation_id IS NULL OR conversation_id = ?) "
            "ORDER BY (importance + MIN(COALESCE(use_count, 0), 5)) DESC, "
            "         COALESCE(last_used_at, created_at) DESC LIMIT ?",
            (user_id, conversation_id or "", limit),
        ).fetchall()
        return [dict(r) for r in rows]


def add_memory(user_id: str, kind: str, content: str, importance: int = 5,
               source_conversation_id: str | None = None,
               category: str | None = None,
               conversation_id: str | None = None) -> dict:
    """Add a memory.

    `category`: model-suggested topical bucket (finance, family, work, tools, ...).
    `conversation_id`: when set, marks this memory as EPISODIC — only injected when
        chatting in that specific conversation. Lifetime memories pass None.
    """
    mid = new_id()
    t = now()
    importance = max(1, min(10, int(importance or 5)))
    with connect() as c:
        c.execute(
            "INSERT INTO memories(id, user_id, kind, content, importance, created_at, "
            "source_conversation_id, category, conversation_id) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (mid, user_id, kind, content, importance, t, source_conversation_id,
             category, conversation_id),
        )
    return {"id": mid, "user_id": user_id, "kind": kind, "content": content,
            "importance": importance, "created_at": t,
            "source_conversation_id": source_conversation_id,
            "category": category, "conversation_id": conversation_id,
            "use_count": 0, "last_used_at": None}


def delete_memory(memory_id: str, user_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM memories WHERE id = ? AND user_id = ?", (memory_id, user_id))


def find_duplicate_memory(user_id: str, content: str) -> dict | None:
    """Dedup by normalized form so '₹40k' and '₹40,000' don't both get stored."""
    norm = normalize_text(content)
    if not norm:
        return None
    with connect() as c:
        rows = c.execute("SELECT * FROM memories WHERE user_id = ?", (user_id,)).fetchall()
    for r in rows:
        if normalize_text(r["content"]) == norm:
            return dict(r)
    return None


def find_duplicate_skill(user_id: str, name: str, *, threshold: float = 0.5) -> dict | None:
    """Dedup skill names by token-set Jaccard ≥ threshold so 'Budget Tracker
    Generator' and 'Household Budget Tracker' collapse instead of breeding.
    """
    target = set(normalize_text(name).split())
    if not target:
        return None
    with connect() as c:
        rows = c.execute("SELECT * FROM skills WHERE user_id = ?", (user_id,)).fetchall()
    for r in rows:
        toks = set(normalize_text(r["name"]).split())
        if not toks:
            continue
        if toks == target:
            return dict(r)
        inter = len(toks & target); union = len(toks | target)
        if union and inter / union >= threshold:
            return dict(r)
    return None


# ---- skills ----

def list_skills(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM skills WHERE user_id = ? ORDER BY is_suggested ASC, use_count DESC, created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_skill(skill_id: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM skills WHERE id = ? AND user_id = ?", (skill_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def add_skill(user_id: str, name: str, description: str | None, prompt_template: str,
              is_suggested: bool = False, body_md: str | None = None) -> dict:
    sid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO skills(id, user_id, name, description, prompt_template, body_md, is_suggested, created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (sid, user_id, name, description, prompt_template, body_md, int(is_suggested), t),
        )
    return {"id": sid, "user_id": user_id, "name": name, "description": description,
            "prompt_template": prompt_template, "body_md": body_md, "use_count": 0,
            "last_used_at": None, "is_suggested": int(is_suggested), "created_at": t}


def update_skill(skill_id: str, user_id: str, *, name: str | None = None,
                 description: str | None = None, prompt_template: str | None = None,
                 body_md: str | None = None, clear_body_md: bool = False,
                 is_suggested: int | None = None,
                 trigger_pattern: str | None = None,
                 clear_trigger_pattern: bool = False) -> None:
    sets, vals = [], []
    if name is not None:
        sets.append("name = ?"); vals.append(name)
    if description is not None:
        sets.append("description = ?"); vals.append(description)
    if prompt_template is not None:
        sets.append("prompt_template = ?"); vals.append(prompt_template)
    if clear_body_md:
        sets.append("body_md = NULL")
    elif body_md is not None:
        sets.append("body_md = ?"); vals.append(body_md)
    if is_suggested is not None:
        sets.append("is_suggested = ?"); vals.append(int(is_suggested))
    if clear_trigger_pattern:
        sets.append("trigger_pattern = NULL")
    elif trigger_pattern is not None:
        sets.append("trigger_pattern = ?"); vals.append(trigger_pattern)
    if not sets:
        return
    vals.extend([skill_id, user_id])
    with connect() as c:
        c.execute(f"UPDATE skills SET {', '.join(sets)} WHERE id = ? AND user_id = ?", vals)


# ---- skill chaining + auto-trigger ----

def list_conversation_skill_ids(conversation_id: str) -> list[str]:
    """All skill IDs attached to this conversation (chained)."""
    with connect() as c:
        rows = c.execute(
            "SELECT skill_id FROM conversation_skills WHERE conversation_id = ? ORDER BY attached_at",
            (conversation_id,),
        ).fetchall()
        return [r["skill_id"] for r in rows]


def attach_skill_to_conversation(conversation_id: str, skill_id: str) -> None:
    with connect() as c:
        c.execute(
            "INSERT OR IGNORE INTO conversation_skills(conversation_id, skill_id, attached_at) "
            "VALUES(?, ?, ?)",
            (conversation_id, skill_id, now()),
        )


def detach_skill_from_conversation(conversation_id: str, skill_id: str) -> None:
    with connect() as c:
        c.execute(
            "DELETE FROM conversation_skills WHERE conversation_id = ? AND skill_id = ?",
            (conversation_id, skill_id),
        )


def list_triggerable_skills(user_id: str) -> list[dict]:
    """Skills with a trigger_pattern set — used by auto-trigger to test against incoming messages."""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM skills WHERE user_id = ? AND is_suggested = 0 "
            "AND trigger_pattern IS NOT NULL AND length(trigger_pattern) > 0",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def bump_skill_use(skill_id: str, user_id: str) -> None:
    with connect() as c:
        c.execute(
            "UPDATE skills SET use_count = use_count + 1, last_used_at = ? WHERE id = ? AND user_id = ?",
            (now(), skill_id, user_id),
        )


def delete_skill(skill_id: str, user_id: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM skills WHERE id = ? AND user_id = ?", (skill_id, user_id))


# ---- skills catalog (GitHub discovery; shared/global) ----

def upsert_catalog_skill(*, source_url: str, repo: str | None, repo_url: str | None,
                         author: str | None, name: str, description: str | None,
                         body_md: str | None, prompt_template: str | None,
                         stars: int, license: str | None, content_hash: str | None) -> None:
    """Insert or refresh a discovered skill, keyed by source_url. Preserves the
    original id, created_at and install_count across refreshes."""
    t = now()
    with connect() as c:
        c.execute(
            """
            INSERT INTO catalog_skills(
                id, source, source_url, repo, repo_url, author, name, description,
                body_md, prompt_template, stars, license, content_hash,
                install_count, fetched_at, created_at)
            VALUES(?, 'github', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(source_url) DO UPDATE SET
                repo=excluded.repo, repo_url=excluded.repo_url, author=excluded.author,
                name=excluded.name, description=excluded.description, body_md=excluded.body_md,
                prompt_template=excluded.prompt_template, stars=excluded.stars,
                license=excluded.license, content_hash=excluded.content_hash,
                fetched_at=excluded.fetched_at
            """,
            (new_id(), source_url, repo, repo_url, author, name, description,
             body_md, prompt_template, int(stars), license, content_hash, t, t),
        )


def list_catalog_skills(query: str | None = None, limit: int = 120) -> list[dict]:
    with connect() as c:
        if query and query.strip():
            like = f"%{query.strip()}%"
            rows = c.execute(
                "SELECT * FROM catalog_skills WHERE name LIKE ? OR description LIKE ? OR repo LIKE ? "
                "ORDER BY stars DESC, install_count DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM catalog_skills ORDER BY stars DESC, install_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_catalog_skill(catalog_id: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM catalog_skills WHERE id = ?", (catalog_id,)).fetchone()
        return dict(row) if row else None


def bump_catalog_install(catalog_id: str) -> None:
    with connect() as c:
        c.execute("UPDATE catalog_skills SET install_count = install_count + 1 WHERE id = ?", (catalog_id,))


def count_catalog_skills() -> int:
    with connect() as c:
        return c.execute("SELECT COUNT(*) AS n FROM catalog_skills").fetchone()["n"]


def catalog_last_refreshed() -> int | None:
    """Unix seconds of the most recent catalog row fetch, or None if empty."""
    with connect() as c:
        row = c.execute("SELECT MAX(fetched_at) AS m FROM catalog_skills").fetchone()
        return row["m"] if row and row["m"] is not None else None


def prune_catalog_stale(before: int) -> int:
    """Delete catalog rows not seen since `before` (unix seconds). Returns count removed."""
    with connect() as c:
        cur = c.execute("DELETE FROM catalog_skills WHERE fetched_at < ?", (before,))
        return cur.rowcount


# ---- search (FTS5) ----

def search_messages(user_id: str, query: str, limit: int = 25) -> list[dict]:
    if not query.strip():
        return []
    # Escape FTS special chars by quoting tokens. SQLite FTS5 needs quoted phrases for safety.
    safe = " ".join(f'"{tok}"' for tok in query.split() if tok)
    if not safe:
        return []
    with connect() as c:
        rows = c.execute(
            """
            SELECT m.id AS message_id, m.conversation_id, m.role, m.created_at,
                   c.title AS conversation_title,
                   snippet(messages_fts, 0, '⟦', '⟧', '…', 12) AS snippet
            FROM messages_fts
            JOIN messages m ON m.id = messages_fts.message_id
            JOIN conversations c ON c.id = m.conversation_id
            WHERE messages_fts.user_id = ? AND messages_fts MATCH ?
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            (user_id, safe, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def list_conversation_files(conversation_id: str, user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM files WHERE conversation_id = ? AND user_id = ? ORDER BY created_at DESC",
            (conversation_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- tasks (per-conversation todo tracker) ----------

_TASK_STATUSES = {"pending", "in_progress", "completed", "cancelled"}


def add_task(conversation_id: str, user_id: str, subject: str,
             description: str | None = None) -> dict:
    tid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO tasks(id, conversation_id, user_id, subject, description, "
            "status, output, created_at, updated_at, completed_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (tid, conversation_id, user_id, subject, description, "pending",
             None, t, t, None),
        )
    return {
        "id": tid, "conversation_id": conversation_id, "user_id": user_id,
        "subject": subject, "description": description, "status": "pending",
        "output": None, "created_at": t, "updated_at": t, "completed_at": None,
    }


def list_tasks(conversation_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM tasks WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_task(task_id: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM tasks WHERE id = ? AND user_id = ?",
            (task_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def update_task(task_id: str, user_id: str, *,
                status: str | None = None,
                subject: str | None = None,
                description: str | None = None) -> dict | None:
    """Returns the updated task row, or None if not found / status invalid."""
    if status is not None and status not in _TASK_STATUSES:
        return None
    sets, vals = ["updated_at = ?"], [now()]
    if status is not None:
        sets.append("status = ?")
        vals.append(status)
        if status in ("completed", "cancelled"):
            sets.append("completed_at = ?")
            vals.append(now())
        else:
            sets.append("completed_at = NULL")
    if subject is not None:
        sets.append("subject = ?")
        vals.append(subject)
    if description is not None:
        sets.append("description = ?")
        vals.append(description)
    vals.extend([task_id, user_id])
    with connect() as c:
        c.execute(
            f"UPDATE tasks SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            vals,
        )
    return get_task(task_id, user_id)


def append_task_output(task_id: str, user_id: str, text: str) -> dict | None:
    cur = get_task(task_id, user_id)
    if not cur:
        return None
    new_out = (cur["output"] + "\n" if cur.get("output") else "") + text
    with connect() as c:
        c.execute(
            "UPDATE tasks SET output = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (new_out, now(), task_id, user_id),
        )
    return get_task(task_id, user_id)


# ---------- schedules (cron-driven prompt runs) ----------

def add_schedule(user_id: str, name: str, cron_expr: str, prompt_text: str,
                 model: str | None = None) -> dict:
    sid = new_id()
    t = now()
    with connect() as c:
        c.execute(
            "INSERT INTO schedules(id, user_id, name, cron_expr, prompt_text, model, "
            "enabled, created_at, last_run_at, last_conversation_id, last_error) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (sid, user_id, name, cron_expr, prompt_text, model, 1, t, None, None, None),
        )
    return {
        "id": sid, "user_id": user_id, "name": name, "cron_expr": cron_expr,
        "prompt_text": prompt_text, "model": model, "enabled": 1,
        "created_at": t, "last_run_at": None, "last_conversation_id": None,
        "last_error": None,
    }


def list_schedules(user_id: str) -> list[dict]:
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM schedules WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_all_enabled_schedules() -> list[dict]:
    """Used by the scheduler at boot to register every active schedule across
    all users. (Single-tenant family chat — there's no tenant fan-out cost.)"""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM schedules WHERE enabled = 1 ORDER BY created_at",
        ).fetchall()
        return [dict(r) for r in rows]


def get_schedule(schedule_id: str, user_id: str) -> dict | None:
    with connect() as c:
        row = c.execute(
            "SELECT * FROM schedules WHERE id = ? AND user_id = ?",
            (schedule_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def update_schedule_status(schedule_id: str, *, last_run_at: int | None = None,
                            last_conversation_id: str | None = None,
                            last_error: str | None = None,
                            clear_error: bool = False) -> None:
    sets, vals = [], []
    if last_run_at is not None:
        sets.append("last_run_at = ?"); vals.append(last_run_at)
    if last_conversation_id is not None:
        sets.append("last_conversation_id = ?"); vals.append(last_conversation_id)
    if clear_error:
        sets.append("last_error = NULL")
    elif last_error is not None:
        sets.append("last_error = ?"); vals.append(last_error)
    if not sets:
        return
    vals.append(schedule_id)
    with connect() as c:
        c.execute(f"UPDATE schedules SET {', '.join(sets)} WHERE id = ?", vals)


def delete_schedule(schedule_id: str, user_id: str) -> bool:
    with connect() as c:
        cur = c.execute(
            "DELETE FROM schedules WHERE id = ? AND user_id = ?",
            (schedule_id, user_id),
        )
        return cur.rowcount > 0
