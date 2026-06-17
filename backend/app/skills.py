"""Skills: per-user reusable prompt recipes (Claude SKILL.md style).

A skill carries:
  • name + description (front-matter style metadata)
  • prompt_template — the user-message text fired when the skill is invoked
  • body_md — Claude-style instructions injected as additional system-prompt context
              for any conversation the skill is attached to

Family members can save a workflow ("weekly meal plan", "summarise my emails")
and re-fire it later, or upload a SKILL.md file.  Some skills are AI-suggested
(is_suggested=1) — the user must accept.
"""
import re

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field

from . import db
from .auth import require_approved_user as require_user

router = APIRouter()


# ---------- frontmatter parser (no PyYAML dep) ----------

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*:\s*(.*)$")


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a Claude-style SKILL.md.  Returns (metadata, body).

    Supports a minimal subset of YAML — `key: value` lines, optional surrounding
    quotes, no nested mappings.  Anything else is ignored so a malformed `.md` is
    never fatal.
    """
    text = text.lstrip("﻿")  # strip BOM if any
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text.strip()
    raw, body = m.group(1), m.group(2)
    meta: dict = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        kv = _KV_RE.match(line)
        if not kv:
            continue
        key, val = kv.group(1).strip().lower(), kv.group(2).strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        meta[key] = val
    return meta, body.strip()


# ---------- request bodies ----------

class CreateSkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=240)
    prompt_template: str = Field(min_length=1, max_length=4000)
    body_md: str | None = Field(default=None, max_length=20000)


class UpdateSkillBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=240)
    prompt_template: str | None = Field(default=None, min_length=1, max_length=4000)
    body_md: str | None = Field(default=None, max_length=20000)
    accept: bool = False  # if true, clears is_suggested
    trigger_pattern: str | None = Field(default=None, max_length=400)
    clear_trigger_pattern: bool = False


class AttachSkillBody(BaseModel):
    conversation_id: str
    skill_id: str


# ---------- routes ----------

@router.get("/skills")
async def list_skills(_: Request, user=Depends(require_user)):
    return {"skills": db.list_skills(user["id"])}


@router.get("/skills/{sid}")
async def get_skill(sid: str, _: Request, user=Depends(require_user)):
    rec = db.get_skill(sid, user["id"])
    if not rec:
        raise HTTPException(404, "not found")
    return rec


@router.post("/skills")
async def create_skill(body: CreateSkillBody, _: Request, user=Depends(require_user)):
    return db.add_skill(
        user["id"],
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        prompt_template=body.prompt_template.strip(),
        body_md=(body.body_md or "").strip() or None,
        is_suggested=False,
    )


@router.post("/skills/upload")
async def upload_skill(file: UploadFile = File(...), user=Depends(require_user)):
    """Upload a Claude-style SKILL.md file.  Front-matter must include at least `name`."""
    raw = await file.read()
    if len(raw) > 200_000:
        raise HTTPException(400, "skill file too large (max 200KB)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "skill file must be UTF-8 text")

    meta, body = _parse_frontmatter(text)
    name = (meta.get("name") or "").strip()
    if not name:
        # Fallback: derive from filename.
        name = (file.filename or "skill").rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip()[:64]
    description = (meta.get("description") or "").strip() or None
    # The first non-heading paragraph of the body is a reasonable default user-message
    # template if the skill author didn't supply one explicitly.
    template = (meta.get("prompt") or meta.get("trigger") or "").strip()
    if not template:
        for para in body.split("\n\n"):
            p = para.strip()
            if p and not p.startswith("#"):
                template = p[:1000]
                break
    if not template:
        template = name
    return db.add_skill(
        user["id"],
        name=name[:64],
        description=description[:240] if description else None,
        prompt_template=template,
        body_md=body or None,
        is_suggested=False,
    )


@router.patch("/skills/{sid}")
async def update_skill(sid: str, body: UpdateSkillBody, _: Request, user=Depends(require_user)):
    rec = db.get_skill(sid, user["id"])
    if not rec:
        raise HTTPException(404, "not found")
    # Validate trigger_pattern is a compileable regex if provided.
    if body.trigger_pattern:
        try:
            import re as _re
            _re.compile(body.trigger_pattern)
        except _re.error as e:
            raise HTTPException(400, f"invalid trigger_pattern regex: {e}")
    db.update_skill(
        sid, user["id"],
        name=body.name.strip() if body.name else None,
        description=(body.description.strip() if body.description is not None else None),
        prompt_template=body.prompt_template.strip() if body.prompt_template else None,
        body_md=(body.body_md.strip() if body.body_md else None),
        is_suggested=0 if body.accept else None,
        trigger_pattern=body.trigger_pattern.strip() if body.trigger_pattern else None,
        clear_trigger_pattern=body.clear_trigger_pattern,
    )
    return db.get_skill(sid, user["id"])


@router.post("/conversations/{cid}/skills/{sid}")
async def attach_skill(cid: str, sid: str, _: Request, user=Depends(require_user)):
    """Attach an additional skill to a conversation (chaining)."""
    if not db.get_conversation(cid, user["id"]):
        raise HTTPException(404, "conversation not found")
    if not db.get_skill(sid, user["id"]):
        raise HTTPException(404, "skill not found")
    db.attach_skill_to_conversation(cid, sid)
    return {"attached": db.list_conversation_skill_ids(cid)}


@router.delete("/conversations/{cid}/skills/{sid}")
async def detach_skill(cid: str, sid: str, _: Request, user=Depends(require_user)):
    if not db.get_conversation(cid, user["id"]):
        raise HTTPException(404, "conversation not found")
    db.detach_skill_from_conversation(cid, sid)
    return {"attached": db.list_conversation_skill_ids(cid)}


@router.get("/conversations/{cid}/skills")
async def list_conversation_skills(cid: str, _: Request, user=Depends(require_user)):
    if not db.get_conversation(cid, user["id"]):
        raise HTTPException(404, "conversation not found")
    ids = db.list_conversation_skill_ids(cid)
    skills = [db.get_skill(s, user["id"]) for s in ids]
    return {"skills": [s for s in skills if s]}


@router.post("/skills/{sid}/use")
async def bump_skill(sid: str, _: Request, user=Depends(require_user)):
    rec = db.get_skill(sid, user["id"])
    if not rec:
        raise HTTPException(404, "not found")
    db.bump_skill_use(sid, user["id"])
    return db.get_skill(sid, user["id"])


@router.delete("/skills/{sid}")
async def delete_skill(sid: str, _: Request, user=Depends(require_user)):
    db.delete_skill(sid, user["id"])
    return {"ok": True}
