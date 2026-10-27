# Document Management & Secure File Sharing API — Day 1

Authentication foundation built with FastAPI, PostgreSQL, SQLAlchemy 2, Alembic, Argon2, and JWT.

## Run with Docker

1. Copy `.env.example` to `.env` and set `SECRET_KEY` to a long random value.
2. Run `docker compose up --build`.
3. Open `http://localhost:8000/docs` for Swagger UI or import `postman/auth.postman_collection.json` into Postman.

The API container applies Alembic migrations before starting. PostgreSQL is exposed on port 5432.

## Run locally

Use Python 3.10 or newer. Install `requirements.txt`, set `DATABASE_URL` and `SECRET_KEY` from `.env.example`, run `alembic upgrade head`, then start the API with `uvicorn app.main:app --reload`. The default local database is SQLite; Docker Compose uses PostgreSQL.

## Authentication flow

- `POST /api/v1/auth/register` — create an account. JSON: `{ "email": "alex@example.com", "full_name": "Alex Morgan", "password": "correct-horse-123" }`. Passwords must contain at least 8 characters.
- `POST /api/v1/auth/login` — JSON `{ "email": "alex@example.com", "password": "correct-horse-123" }`; returns `{ "access_token": "...", "token_type": "bearer" }`.
- `GET /api/v1/auth/me` — protected endpoint; send `Authorization: Bearer <access_token>`.
- `GET /health` — liveness endpoint.

## Documents (Day 2)

All document routes require a bearer access token. Uploaded bytes are stored outside the web root with a random server-generated filename. The original name is retained as metadata. The API accepts PDF, plain text, PNG, JPEG, GIF, and DOCX files when both extension and declared content type match; each file is limited to 10 MiB by default. Configure `MAX_UPLOAD_SIZE_BYTES`, `MAX_UPLOAD_BATCH_COUNT`, and `DOCUMENT_STORAGE_DIR` to change these defaults.

- `POST /api/v1/documents` — multipart form with one `file` field.
- `POST /api/v1/documents/bulk` — multipart form with repeated `files` fields (maximum 10).
- `GET /api/v1/documents` — list your documents. Supports `page`, `page_size` (max 100), `search`, `media_type`, `extension`, `uploaded_after`, `uploaded_before`, `sort_by` (`uploaded_at`, `filename`, or `size`), and `sort_order` (`asc` or `desc`).
- `GET /api/v1/documents/{id}` — read owned document metadata.
- `GET /api/v1/documents/{id}/download` — download an owned file.
- `DELETE /api/v1/documents/{id}` — delete owned metadata and file bytes.

The `documents` table is created by the next Alembic migration. Docker Compose persists uploaded content in its `document_files` volume. Import `postman/auth.postman_collection.json` for the auth calls, and `postman/documents.postman_collection.json` for upload, list, download, and delete requests. Select a local file in the Postman form-data field before sending uploads.

Registration normalizes email to lowercase, rejects duplicate addresses, and never returns a password hash. New accounts receive the `member` role; role assignment is intentionally not exposed through public registration. JWT lifetime is configurable with `ACCESS_TOKEN_EXPIRE_MINUTES` (default 30).

## Postman checks

Import the collection, then run Register user, Login, and Current user in order. Login saves the returned token to the collection variable. To verify protection, clear the `token` variable and run Current user; it should return 401. Re-registering the same email should return 409, and a wrong password should return 401.
