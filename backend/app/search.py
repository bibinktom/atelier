"""Cross-conversation full-text search (SQLite FTS5)."""
from fastapi import APIRouter, Depends, Query, Request

from . import db
from .auth import require_approved_user as require_user

router = APIRouter()


@router.get("/search")
async def search_messages(
    _: Request,
    q: str = Query("", description="Search query"),
    limit: int = Query(25, ge=1, le=100),
    user=Depends(require_user),
):
    rows = db.search_messages(user["id"], q, limit=limit)
    return {"results": rows, "query": q}


@router.get("/memories")
async def list_memories(_: Request, user=Depends(require_user)):
    return {"memories": db.list_memories(user["id"])}


@router.delete("/memories/{mid}")
async def delete_memory(mid: str, _: Request, user=Depends(require_user)):
    db.delete_memory(mid, user["id"])
    return {"ok": True}
