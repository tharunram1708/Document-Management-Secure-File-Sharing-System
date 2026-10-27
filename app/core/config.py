import os
from pathlib import Path


class Settings:
    """Environment-backed application settings."""

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./documents.db")
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-this-secret-key-please")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    document_storage_dir: Path = Path(os.getenv("DOCUMENT_STORAGE_DIR", "storage"))
    max_upload_size_bytes: int = int(os.getenv("MAX_UPLOAD_SIZE_BYTES", str(10 * 1024 * 1024)))
    max_upload_batch_count: int = int(os.getenv("MAX_UPLOAD_BATCH_COUNT", "10"))
    app_name: str = "Document Sharing API"


settings = Settings()
