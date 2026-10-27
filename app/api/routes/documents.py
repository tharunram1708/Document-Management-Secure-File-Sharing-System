import logging
from datetime import datetime
from math import ceil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models import Document, User
from app.schemas.document import DocumentPage, DocumentRead
from app.services.document_storage import ALLOWED_TYPES, resolve_stored_file, store_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


def _save_document_records(files: list[dict], owner: User, db: Session) -> list[Document]:
    documents = [
        Document(
            owner_id=owner.id,
            original_filename=file["original_filename"],
            stored_filename=file["stored_filename"],
            media_type=file["media_type"],
            size_bytes=file["size_bytes"],
            sha256=file["sha256"],
        )
        for file in files
    ]
    try:
        db.add_all(documents)
        db.commit()
        for document in documents:
            db.refresh(document)
        return documents
    except Exception:
        db.rollback()
        for file in files:
            file["path"].unlink(missing_ok=True)
        raise


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stored = await store_upload(file)
    return _save_document_records([stored], current_user, db)[0]


@router.post("/bulk", response_model=list[DocumentRead], status_code=status.HTTP_201_CREATED)
async def upload_documents(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required")
    if len(files) > settings.max_upload_batch_count:
        raise HTTPException(status_code=413, detail=f"A maximum of {settings.max_upload_batch_count} files may be uploaded at once")
    stored_files: list[dict] = []
    try:
        for upload in files:
            stored_files.append(await store_upload(upload))
        return _save_document_records(stored_files, current_user, db)
    except Exception:
        for upload in files:
            await upload.close()
        for file in stored_files:
            file["path"].unlink(missing_ok=True)
        raise


@router.get("", response_model=DocumentPage)
def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, max_length=255),
    media_type: str | None = Query(default=None, max_length=120),
    extension: str | None = Query(default=None, max_length=10),
    uploaded_after: datetime | None = None,
    uploaded_before: datetime | None = None,
    sort_by: str = Query(default="uploaded_at", pattern="^(uploaded_at|filename|size)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if uploaded_after and uploaded_before and uploaded_after > uploaded_before:
        raise HTTPException(status_code=422, detail="uploaded_after must be earlier than uploaded_before")
    filters = [Document.owner_id == current_user.id]
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        filters.append(Document.original_filename.ilike(f"%{escaped}%", escape="\\"))
    if media_type:
        filters.append(Document.media_type == media_type.lower())
    if extension:
        normalized_extension = extension.lower().lstrip(".")
        if f".{normalized_extension}" not in ALLOWED_TYPES:
            raise HTTPException(status_code=422, detail="Unsupported extension filter")
        filters.append(func.lower(Document.original_filename).like(f"%.{normalized_extension}"))
    if uploaded_after:
        filters.append(Document.created_at >= uploaded_after)
    if uploaded_before:
        filters.append(Document.created_at <= uploaded_before)

    total = db.scalar(select(func.count()).select_from(Document).where(*filters)) or 0
    sort_column = {
        "uploaded_at": Document.created_at,
        "filename": func.lower(Document.original_filename),
        "size": Document.size_bytes,
    }[sort_by]
    ordering = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    items = db.scalars(
        select(Document)
        .where(*filters)
        .order_by(ordering, Document.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return DocumentPage(items=items, total=total, page=page, page_size=page_size, pages=ceil(total / page_size))


def _owned_document(document_id: int, owner_id: int, db: Session) -> Document:
    document = db.scalar(select(Document).where(Document.id == document_id, Document.owner_id == owner_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _owned_document(document_id, current_user.id, db)


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = _owned_document(document_id, current_user.id, db)
    path = resolve_stored_file(document.stored_filename)
    return FileResponse(path, media_type=document.media_type, filename=Path(document.original_filename).name, content_disposition_type="attachment")


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = _owned_document(document_id, current_user.id, db)
    try:
        path = resolve_stored_file(document.stored_filename)
    except HTTPException:
        path = None
    db.delete(document)
    db.commit()
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to remove stored document %s after database deletion", document.id)
