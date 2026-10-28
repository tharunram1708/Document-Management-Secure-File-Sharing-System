from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.core.roles import SharePermission


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentRead]
    total: int
    page: int
    page_size: int
    pages: int


class DocumentShareCreate(BaseModel):
    email: str
    permission: SharePermission = SharePermission.READ


class DocumentShareRead(BaseModel):
    user_id: int
    email: str
    permission: SharePermission
