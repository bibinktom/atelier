"""User-facing identity.md.

Surfaces the per-user `memories` table as an editable markdown file. The
canonical store is still SQLite — this module is a renderer + parser.
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from . import memory
from .auth import require_user

router = APIRouter(prefix="/me", tags=["identity"])


class IdentityIn(BaseModel):
    markdown: str


@router.get("/identity")
def get_identity(_: Request, user=Depends(require_user)) -> dict:
    return {"markdown": memory.render_identity_markdown(user["id"])}


@router.put("/identity")
def put_identity(body: IdentityIn, _: Request, user=Depends(require_user)) -> dict:
    items = memory.parse_identity_markdown(body.markdown)
    count = memory.replace_identity_memories(user["id"], items)
    return {"ok": True, "count": count,
            "markdown": memory.render_identity_markdown(user["id"])}
