"""Per-user memory + self-improving learning loop.

After conversations, an extractor reads the transcript and proposes:
  • memories: durable facts/preferences/lessons about the user
  • skill_candidates: tasks that look reusable

Memories are injected into future system prompts; skill candidates show up in the
sidebar for the user to accept or dismiss.
"""
import json
import re
from typing import Any

import httpx

from . import config, db, nim


# ---------- prompt injection helpers ----------

MEMORY_HEADER = (
    "What you've learned about this user from past conversations (use silently — don't recite back unless asked):"
)


def memory_block(user_id: str, *, limit: int = 12,
                 conversation_id: str | None = None) -> str:
    """Render the user's top memories for system-prompt injection.

    `conversation_id` enables episodic memory: items scoped to that conversation
    join the global lifetime memories. Each rendered memory is marked as used so
    promotion keeps frequently-cited memories at the top.
    """
    items = db.top_memories(user_id, limit=limit, conversation_id=conversation_id)
    if not items:
        return ""
    db.mark_memories_used([m["id"] for m in items])
    lines = []
    for m in items:
        kind = m["kind"].upper() if m.get("kind") else "FACT"
        cat = f" {{{m['category']}}}" if m.get("category") else ""
        scope = " (this conversation)" if m.get("conversation_id") else ""
        lines.append(f"  - [{kind}{cat}{scope}] {m['content']}")
    return f"\n\n{MEMORY_HEADER}\n" + "\n".join(lines)


# ---------- editable identity (per-user file view of memories) ----------

# Order categories are rendered in. Friendly labels for the UI; the underlying
# DB `category` value stays the canonical token.
_CATEGORY_HEADINGS: list[tuple[str, str]] = [
    ("general", "About me"),
    ("family", "Family"),
    ("work", "Work"),
    ("finance", "Money"),
    ("preference", "Preferences"),
    ("tools", "Tools & workflows"),
]
_HEADING_TO_CATEGORY = {label: key for key, label in _CATEGORY_HEADINGS}
_VALID_CATEGORIES = {key for key, _ in _CATEGORY_HEADINGS}


def render_identity_markdown(user_id: str) -> str:
    """Render the user's lifetime memories as an editable identity.md.

    Episodic memories (conversation-scoped) are deliberately excluded — they're
    not "who the user is", they're "what we were just doing".
    """
    rows = db.list_memories(user_id, limit=400)
    rows = [r for r in rows if not r.get("conversation_id")]
    by_cat: dict[str, list[dict]] = {key: [] for key, _ in _CATEGORY_HEADINGS}
    for r in rows:
        cat = (r.get("category") or "general").lower()
        if cat not in by_cat:
            cat = "general"
        by_cat[cat].append(r)

    out: list[str] = ["# Who I am",
                      "",
                      "Edit freely — each `- ` bullet becomes a memory the AI uses in every reply.",
                      ""]
    for key, label in _CATEGORY_HEADINGS:
        items = by_cat.get(key) or []
        if not items:
            continue
        out.append(f"## {label}")
        for it in items:
            out.append(f"- {it['content'].strip()}")
        out.append("")
    if all(not v for v in by_cat.values()):
        out.append("_No memories yet — chat with the assistant or edit this file directly._")
    return "\n".join(out).rstrip() + "\n"


_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")
_HEADING_RE = re.compile(r"^\s*##\s+(.+?)\s*$")


def parse_identity_markdown(text: str) -> list[dict]:
    """Parse identity.md back into memory items.

    Each `## Heading` switches the active category (matched against the
    rendered labels — anything else maps to "general"). Each `- bullet` becomes
    a memory under that category.
    """
    items: list[dict] = []
    seen_norm: set[str] = set()
    current_cat = "general"
    for raw in (text or "").splitlines():
        h = _HEADING_RE.match(raw)
        if h:
            label = h.group(1).strip()
            current_cat = _HEADING_TO_CATEGORY.get(label, "general")
            continue
        b = _BULLET_RE.match(raw)
        if not b:
            continue
        content = b.group(1).strip()
        if len(content) < 2:
            continue
        norm = db.normalize_text(content)
        if not norm or norm in seen_norm:
            continue
        seen_norm.add(norm)
        items.append({"content": content, "category": current_cat})
    return items


def replace_identity_memories(user_id: str, items: list[dict]) -> int:
    """Replace the user's lifetime (non-episodic) memories with `items`.

    Episodic memories are preserved. Returns the number of memories written.
    """
    with db.connect() as c:
        c.execute(
            "DELETE FROM memories WHERE user_id = ? AND conversation_id IS NULL",
            (user_id,),
        )
    for it in items:
        cat = it.get("category") or "general"
        if cat not in _VALID_CATEGORIES:
            cat = "general"
        db.add_memory(user_id, kind="fact", content=it["content"],
                      importance=6, category=cat)
    return len(items)


# ---------- extraction ----------

EXTRACTION_PROMPT = """You are a memory-extraction helper. Read the conversation between a user and their AI assistant. Extract:

1) `memories` — facts, preferences, or lessons that help FUTURE work.
   Each item has:
     - kind: one of "fact" | "preference" | "lesson" | "tool_pattern"
        - "fact": a stable fact about the user/their family/situation.
        - "preference": a style/working preference (e.g., "Writes in Australian English").
        - "lesson": a tactical lesson (e.g., "When this user says 'deck', they mean PPT ≤ 10 slides").
        - "tool_pattern": a repeatable assistant behavior (e.g., "When user asks for budgets, default to xlsx not pdf").
     - content: one short line.
     - importance: 1-10. Skip anything trivial or one-off.
     - category: one of "finance" | "family" | "work" | "tools" | "preference" | "general"
       (best-fit topical bucket; use "general" if unsure).
     - episodic: bool. True ONLY if the memory is specific to THIS conversation's task
       (e.g., "this conversation is about debugging the auth flow"). Lifetime facts
       about the user are episodic=false.

2) `skill_candidates` — only if the user asked for something that looks reusable (recurring task pattern). One-off requests are NOT candidates.
   Each: name (3-6 words), description (one line), prompt (a fully-written prompt template the user could re-fire later).

Output JSON ONLY, matching this schema. Use empty arrays if nothing useful.

{
  "memories": [{"kind": "fact|preference|lesson|tool_pattern", "content": "...",
                 "importance": 1-10, "category": "...", "episodic": false}],
  "skill_candidates": [{"name": "...", "description": "...", "prompt": "..."}]
}
"""


def _format_transcript(messages: list[dict], max_chars: int = 8000) -> str:
    """Compact transcript for the extractor — drop tool-call mechanics, keep user/assistant text."""
    parts = []
    for m in messages:
        c = m["content"]
        role = m["role"]
        if role == "user":
            text = c if isinstance(c, str) else (c.get("text") if isinstance(c, dict) else "")
            if text:
                parts.append(f"USER: {text}")
        elif role == "assistant":
            if isinstance(c, str):
                if c.strip():
                    parts.append(f"ASSISTANT: {c}")
            elif isinstance(c, dict):
                txt = c.get("content") or ""
                if txt.strip():
                    parts.append(f"ASSISTANT: {txt}")
                for tc in (c.get("tool_calls") or []):
                    fn = (tc.get("function") or {})
                    args = fn.get("arguments", "")
                    if isinstance(args, str) and len(args) > 200:
                        args = args[:200] + "…"
                    parts.append(f"  (used tool: {fn.get('name')}({args}))")
    transcript = "\n".join(parts)
    if len(transcript) > max_chars:
        transcript = transcript[:max_chars] + "\n…[truncated]"
    return transcript


JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```")


def _parse_json_lenient(text: str) -> dict | None:
    text = text.strip()
    # Strip markdown fences if present
    fence = JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # Carve out the first {...} block
    start = text.find("{"); end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


async def _call_extractor(transcript: str, model: str,
                          creds: "nim.LLMCreds | None" = None) -> dict | None:
    base_url, api_key, upstream_model, provider = nim.route(model, creds)
    body: dict = {
        "model": upstream_model,
        "messages": [
            {"role": "system", "content": EXTRACTION_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.1,
        "max_tokens": 1500,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{base_url}/chat/completions"
    timeout = httpx.Timeout(connect=10.0, read=60.0, write=20.0, pool=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(url, headers=headers, json=body)
        except httpx.RequestError as e:
            print(f"[memory] extractor transport error: {e}")
            return None
    if resp.status_code >= 400:
        print(f"[memory] extractor HTTP {resp.status_code}: {resp.text[:300]}")
        # Some models reject response_format; retry without it.
        if "response_format" in resp.text or resp.status_code == 400:
            body.pop("response_format", None)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp2 = await client.post(url, headers=headers, json=body)
            if resp2.status_code >= 400:
                return None
            resp = resp2
        else:
            return None
    try:
        text = resp.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, json.JSONDecodeError):
        return None
    return _parse_json_lenient(text)


async def extract_from_conversation(conversation_id: str, user_id: str, *,
                                    extractor_model: str | None = None,
                                    creds: "nim.LLMCreds | None" = None) -> dict[str, int]:
    """Run extraction on a conversation. Returns counts of items added.
    Idempotent — only processes messages added since the conversation's last_extracted_at.

    `creds` carries the user's provider key (this runs as a background task outside
    the turn's context). If the user has no usable provider, skip silently."""
    if creds is not None and creds.openrouter_key is None and not creds.allow_nim_fallback:
        return {"memories": 0, "skill_candidates": 0}
    conv = db.get_conversation(conversation_id, user_id)
    if not conv:
        return {"memories": 0, "skill_candidates": 0}

    messages = db.list_messages(conversation_id)
    last_ext = conv.get("last_extracted_at") or 0
    fresh = [m for m in messages if m["created_at"] > last_ext]
    # Need at least one user + one assistant turn since last extraction.
    if len(fresh) < 2 or not any(m["role"] == "user" for m in fresh):
        return {"memories": 0, "skill_candidates": 0}
    if not any(m["role"] == "assistant" for m in fresh):
        return {"memories": 0, "skill_candidates": 0}

    # Send the WHOLE conversation each time so the extractor sees full context, but only commit
    # if the fresh slice has substance. (Keeps memories coherent across multi-turn arcs.)
    transcript = _format_transcript(messages)
    if not transcript.strip():
        return {"memories": 0, "skill_candidates": 0}

    model = extractor_model or config.MEMORY_EXTRACTOR_MODEL
    try:
        parsed = await _call_extractor(transcript, model, creds)
    except nim.LLMNotConnected:
        return {"memories": 0, "skill_candidates": 0}

    # Mark processed regardless — failed extractions shouldn't loop forever.
    with db.connect() as c:
        c.execute("UPDATE conversations SET last_extracted_at = ? WHERE id = ?",
                  (db.now(), conversation_id))

    if not parsed:
        return {"memories": 0, "skill_candidates": 0}

    valid_kinds = {"fact", "preference", "lesson", "tool_pattern"}
    valid_categories = {"finance", "family", "work", "tools", "preference", "general"}

    mems_added = 0
    for m in (parsed.get("memories") or [])[:8]:
        content = (m.get("content") or "").strip()
        kind = (m.get("kind") or "fact").lower()
        if kind not in valid_kinds:
            kind = "fact"
        if not content or len(content) < 6:
            continue
        if db.find_duplicate_memory(user_id, content):
            continue
        try:
            importance = int(m.get("importance") or 5)
        except (TypeError, ValueError):
            importance = 5
        category = (m.get("category") or "general").lower()
        if category not in valid_categories:
            category = "general"
        # `bool("false")` is True — coerce string flags from sloppy extractors.
        ep_raw = m.get("episodic")
        if isinstance(ep_raw, str):
            episodic = ep_raw.strip().lower() in ("true", "1", "yes")
        else:
            episodic = bool(ep_raw)
        db.add_memory(
            user_id, kind=kind, content=content, importance=importance,
            source_conversation_id=conversation_id,
            category=category,
            conversation_id=conversation_id if episodic else None,
        )
        mems_added += 1

    # Memory hygiene — runs at most once per extraction; cheap (single SQL pass).
    db.decay_memories(user_id)

    skills_added = 0
    for s in (parsed.get("skill_candidates") or [])[:4]:
        name = (s.get("name") or "").strip()
        prompt = (s.get("prompt") or "").strip()
        if not name or not prompt:
            continue
        # Skip near-duplicate names per user (token-set Jaccard, see db.find_duplicate_skill).
        if db.find_duplicate_skill(user_id, name):
            continue
        db.add_skill(user_id, name=name, description=(s.get("description") or "").strip() or None,
                     prompt_template=prompt, is_suggested=True)
        skills_added += 1

    return {"memories": mems_added, "skill_candidates": skills_added}
