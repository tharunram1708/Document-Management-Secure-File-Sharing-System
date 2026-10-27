import asyncio
import codecs
import hashlib
import os
import re
import tempfile
import uuid
import zipfile
from pathlib import Path

import aiofiles
from fastapi import HTTPException, UploadFile

from app.core.config import settings

CHUNK_SIZE = 64 * 1024
ALLOWED_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _validate_filename_and_type(upload: UploadFile) -> tuple[str, str, str]:
    submitted_name = (upload.filename or "").replace("\\", "/")
    original_name = submitted_name.rsplit("/", maxsplit=1)[-1].strip()
    if (
        not original_name
        or original_name in {".", ".."}
        or len(original_name) > 255
        or any(ord(character) < 32 or ord(character) == 127 for character in original_name)
    ):
        raise HTTPException(status_code=400, detail="A valid filename of at most 255 characters is required")

    extension = Path(original_name).suffix.lower()
    expected_type = ALLOWED_TYPES.get(extension)
    declared_type = (upload.content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if expected_type is None:
        raise HTTPException(status_code=415, detail="File extension is not supported")
    if declared_type != expected_type:
        raise HTTPException(status_code=415, detail="File content type does not match its extension")
    return original_name, extension, expected_type


def _validate_content(path: Path, extension: str) -> None:
    with path.open("rb") as source:
        prefix = source.read(16)

    signatures = {
        ".pdf": prefix.startswith(b"%PDF-"),
        ".png": prefix.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": prefix.startswith(b"\xff\xd8\xff"),
        ".jpeg": prefix.startswith(b"\xff\xd8\xff"),
        ".gif": prefix.startswith((b"GIF87a", b"GIF89a")),
    }
    if extension in signatures and not signatures[extension]:
        raise HTTPException(status_code=415, detail="File content does not match its extension")
    if extension == ".txt":
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    if b"\x00" in chunk:
                        raise UnicodeError("NUL byte in text")
                    decoder.decode(chunk)
                decoder.decode(b"", final=True)
        except UnicodeError:
            raise HTTPException(status_code=415, detail="Text files must contain valid UTF-8 text") from None
    if extension == ".docx":
        try:
            with zipfile.ZipFile(path) as archive:
                if "word/document.xml" not in archive.namelist():
                    raise HTTPException(status_code=415, detail="DOCX content is invalid")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=415, detail="DOCX content is invalid") from None


async def store_upload(upload: UploadFile) -> dict:
    original_name, extension, media_type = _validate_filename_and_type(upload)
    storage_dir = settings.document_storage_dir.resolve()
    storage_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=storage_dir)
    os.close(fd)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    size = 0
    try:
        async with aiofiles.open(temp_path, "wb") as output:
            while chunk := await upload.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.max_upload_size_bytes:
                    raise HTTPException(status_code=413, detail="File exceeds the configured size limit")
                digest.update(chunk)
                await output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="Empty files are not allowed")
        await asyncio.to_thread(_validate_content, temp_path, extension)

        stored_filename = f"{uuid.uuid4().hex}{extension}"
        final_path = storage_dir / stored_filename
        os.replace(temp_path, final_path)
        return {
            "original_filename": original_name,
            "stored_filename": stored_filename,
            "media_type": media_type,
            "size_bytes": size,
            "sha256": digest.hexdigest(),
            "path": final_path,
        }
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()


def resolve_stored_file(stored_filename: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}\.[a-z0-9]+", stored_filename):
        raise HTTPException(status_code=404, detail="Document file not found")
    storage_dir = settings.document_storage_dir.resolve()
    path = (storage_dir / stored_filename).resolve()
    if path.parent != storage_dir or not path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found")
    return path
