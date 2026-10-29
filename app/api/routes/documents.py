import logging
import hashlib
import json
from datetime import datetime
from math import ceil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_roles
from app.core.config import settings
from app.core.roles import Role, SharePermission
from app.db.session import get_db
from app.models import Document, DocumentShare, User
from app.schemas.document import DocumentPage, DocumentRead, DocumentShareCreate, DocumentShareRead
from app.services.document_storage import ALLOWED_TYPES, resolve_stored_file, store_upload
from app.services.audit import record_audit
from app.services.cache import get_json, invalidate_documents, set_json
from app.services.notifications import notify_upload

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
        db.flush()
        record_audit(db, "document.upload", actor_id=owner.id, resource_type="document", details={"document_ids": [item.id for item in documents], "filenames": [item.original_filename for item in documents]})
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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.USER)),
    db: Session = Depends(get_db),
):
    stored = await store_upload(file)
    document = _save_document_records([stored], current_user, db)[0]
    invalidate_documents()
    background_tasks.add_task(notify_upload, current_user.email, [document.original_filename])
    return document


@router.post("/bulk", response_model=list[DocumentRead], status_code=status.HTTP_201_CREATED)
async def upload_documents(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    current_user: User = Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.USER)),
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
        documents = _save_document_records(stored_files, current_user, db)
        invalidate_documents()
        background_tasks.add_task(notify_upload, current_user.email, [document.original_filename for document in documents])
        return documents
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
    filters = [or_(Document.owner_id == current_user.id, Document.shares.any(DocumentShare.user_id == current_user.id))]
    if current_user.role in (Role.ADMIN.value, Role.MANAGER.value):
        filters = [True]
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

    cache_params = {"user": current_user.id, "role": current_user.role, "page": page, "page_size": page_size, "search": search, "media_type": media_type, "extension": extension, "after": uploaded_after.isoformat() if uploaded_after else None, "before": uploaded_before.isoformat() if uploaded_before else None, "sort_by": sort_by, "sort_order": sort_order}
    cache_key = "documents:v1:" + hashlib.sha256(json.dumps(cache_params, sort_keys=True).encode()).hexdigest()
    cached = get_json(cache_key)
    if cached is not None:
        return DocumentPage.model_validate(cached)
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
    result = DocumentPage(items=items, total=total, page=page, page_size=page_size, pages=ceil(total / page_size))
    set_json(cache_key, result.model_dump(mode="json"))
    return result


def _accessible_document(document_id: int, current_user: User, db: Session, *, download: bool = False) -> Document:
    permitted = Document.owner_id == current_user.id
    if current_user.role in (Role.ADMIN.value, Role.MANAGER.value):
        permitted = True
    else:
        permissions = [SharePermission.DOWNLOAD.value] if download else [SharePermission.READ.value, SharePermission.DOWNLOAD.value]
        permitted = or_(permitted, Document.shares.any(
            (DocumentShare.user_id == current_user.id) & (DocumentShare.permission.in_(permissions))
        ))
    document = db.scalar(select(Document).where(Document.id == document_id, permitted))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _accessible_document(document_id, current_user, db)


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = _accessible_document(document_id, current_user, db, download=True)
    path = resolve_stored_file(document.stored_filename)
    return FileResponse(path, media_type=document.media_type, filename=Path(document.original_filename).name, content_disposition_type="attachment")


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.scalar(select(Document).where(Document.id == document_id)) if current_user.role == Role.ADMIN.value else db.scalar(select(Document).where(Document.id == document_id, Document.owner_id == current_user.id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        path = resolve_stored_file(document.stored_filename)
    except HTTPException:
        path = None
    db.delete(document)
    record_audit(db, "document.delete", actor_id=current_user.id, resource_type="document", resource_id=document.id, details={"filename": document.original_filename})
    db.commit()
    invalidate_documents()
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to remove stored document %s after database deletion", document.id)


@router.put("/{document_id}/shares", response_model=DocumentShareRead, status_code=status.HTTP_200_OK)
def share_document(
    document_id: int,
    payload: DocumentShareCreate,
    current_user: User = Depends(require_roles(Role.ADMIN, Role.MANAGER, Role.USER)),
    db: Session = Depends(get_db),
):
    document = db.scalar(select(Document).where(Document.id == document_id))
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if current_user.role != Role.ADMIN.value and document.owner_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    recipient = db.scalar(select(User).where(func.lower(User.email) == payload.email.lower()))
    if recipient is None or recipient.id == document.owner_id:
        raise HTTPException(status_code=404, detail="Recipient not found")
    share = db.scalar(select(DocumentShare).where(DocumentShare.document_id == document.id, DocumentShare.user_id == recipient.id))
    if share is None:
        share = DocumentShare(document_id=document.id, user_id=recipient.id)
        db.add(share)
    share.permission = payload.permission.value
    record_audit(db, "document.share", actor_id=current_user.id, resource_type="document", resource_id=document.id, details={"recipient_id": recipient.id, "permission": payload.permission.value})
    db.commit()
    invalidate_documents()
    return DocumentShareRead(user_id=recipient.id, email=recipient.email, permission=payload.permission)


@router.get("/{document_id}/shares", response_model=list[DocumentShareRead])
def list_document_shares(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.scalar(select(Document).where(Document.id == document_id))
    if document is None or (current_user.role != Role.ADMIN.value and document.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")
    shares = db.scalars(select(DocumentShare).where(DocumentShare.document_id == document_id)).all()
    return [DocumentShareRead(user_id=share.user.id, email=share.user.email, permission=share.permission) for share in shares]


@router.delete("/{document_id}/shares/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_document_share(
    document_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document = db.scalar(select(Document).where(Document.id == document_id))
    if document is None or (current_user.role != Role.ADMIN.value and document.owner_id != current_user.id):
        raise HTTPException(status_code=404, detail="Document not found")
    share = db.scalar(select(DocumentShare).where(DocumentShare.document_id == document_id, DocumentShare.user_id == user_id))
    if share is None:
        raise HTTPException(status_code=404, detail="Share not found")
    db.delete(share)
    record_audit(db, "document.share.revoke", actor_id=current_user.id, resource_type="document", resource_id=document.id, details={"recipient_id": user_id})
    db.commit()
    invalidate_documents()
