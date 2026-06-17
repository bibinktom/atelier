import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from . import config, db
from .auth import require_approved_user as require_user

router = APIRouter()

ALLOWED_IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10MB


@router.post("/uploads/image")
async def upload_image(file: UploadFile = File(...), user=Depends(require_user)):
    mime = (file.content_type or "").lower()
    if mime not in ALLOWED_IMAGE_MIMES:
        raise HTTPException(400, f"unsupported image type: {mime}")
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image too large (max 10MB)")
    if not data:
        raise HTTPException(400, "empty file")

    os.makedirs(config.FILES_DIR, exist_ok=True)
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}[mime]
    fname_disk = f"upload_{uuid.uuid4().hex}{ext}"
    path = os.path.join(config.FILES_DIR, fname_disk)
    with open(path, "wb") as f:
        f.write(data)

    rec = db.add_file(
        user_id=user["id"],
        conversation_id=None,
        filename=file.filename or fname_disk,
        path=path,
        mime=mime,
        size=len(data),
    )
    return {"file_id": rec["id"], "filename": rec["filename"], "mime": mime, "size": len(data)}
