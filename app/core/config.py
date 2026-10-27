import os


class Settings:
    """Environment-backed application settings."""

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./documents.db")
    secret_key: str = os.getenv("SECRET_KEY", "dev-only-change-this-secret-key-please")
    algorithm: str = "HS256"
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    app_name: str = "Document Sharing API"


settings = Settings()
