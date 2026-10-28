from fastapi.testclient import TestClient

from app.core.config import settings


def _register(client: TestClient, email: str) -> str:
    payload = {"email": email, "full_name": "Document Owner", "password": "correct-horse-123"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    response = client.post("/api/v1/auth/login", json={"email": email, "password": payload["password"]})
    assert response.status_code == 200
    return response.json()["access_token"]


def test_single_upload_metadata_download_and_delete(client):
    token = _register(client, "owner@example.com")
    auth = {"Authorization": f"Bearer {token}"}

    uploaded = client.post(
        "/api/v1/documents",
        headers=auth,
        files={"file": ("notes.txt", b"hello secure world", "text/plain")},
    )
    assert uploaded.status_code == 201
    document = uploaded.json()
    assert document["original_filename"] == "notes.txt"
    assert document["size_bytes"] == len(b"hello secure world")
    assert "stored_filename" not in document

    listed = client.get("/api/v1/documents?page=1&page_size=5&extension=txt&search=notes&sort_by=filename", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == document["id"]

    download = client.get(f"/api/v1/documents/{document['id']}/download", headers=auth)
    assert download.status_code == 200
    assert download.content == b"hello secure world"
    assert "attachment" in download.headers["content-disposition"]

    deleted = client.delete(f"/api/v1/documents/{document['id']}", headers=auth)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/documents/{document['id']}", headers=auth).status_code == 404
    assert list((settings.document_storage_dir).glob("*")) == []


def test_bulk_upload_validation_and_owner_isolation(client, monkeypatch):
    owner_token = _register(client, "owner@example.com")
    other_token = _register(client, "other@example.com")
    owner_auth = {"Authorization": f"Bearer {owner_token}"}
    other_auth = {"Authorization": f"Bearer {other_token}"}

    bulk = client.post(
        "/api/v1/documents/bulk",
        headers=owner_auth,
        files=[
            ("files", ("one.txt", b"first", "text/plain")),
            ("files", ("two.txt", b"second", "text/plain")),
        ],
    )
    assert bulk.status_code == 201
    ids = [item["id"] for item in bulk.json()]
    assert len(ids) == 2
    assert client.get("/api/v1/documents", headers=other_auth).json()["total"] == 0
    assert client.get(f"/api/v1/documents/{ids[0]}", headers=other_auth).status_code == 404
    assert client.delete(f"/api/v1/documents/{ids[0]}", headers=other_auth).status_code == 404
    page = client.get(
        "/api/v1/documents?page=1&page_size=1&extension=txt&sort_by=filename&sort_order=asc",
        headers=owner_auth,
    ).json()
    assert page["total"] == 2
    assert page["pages"] == 2
    assert page["items"][0]["original_filename"] == "one.txt"

    unsupported = client.post(
        "/api/v1/documents",
        headers=owner_auth,
        files={"file": ("payload.exe", b"MZ", "application/octet-stream")},
    )
    assert unsupported.status_code == 415
    wrong_mime = client.post(
        "/api/v1/documents",
        headers=owner_auth,
        files={"file": ("notes.txt", b"valid text", "application/pdf")},
    )
    assert wrong_mime.status_code == 415

    monkeypatch.setattr(settings, "max_upload_size_bytes", 100)
    mismatch = client.post(
        "/api/v1/documents",
        headers=owner_auth,
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert mismatch.status_code == 415
    monkeypatch.setattr(settings, "max_upload_size_bytes", 4)
    too_large = client.post(
        "/api/v1/documents",
        headers=owner_auth,
        files={"file": ("large.txt", b"12345", "text/plain")},
    )
    assert too_large.status_code == 413


def test_document_sharing_read_download_revoke_and_role_restrictions(client):
    owner = _register(client, "owner@example.com")
    recipient = _register(client, "recipient@example.com")
    owner_auth = {"Authorization": f"Bearer {owner}"}
    recipient_auth = {"Authorization": f"Bearer {recipient}"}
    upload = client.post("/api/v1/documents", headers=owner_auth,
                         files={"file": ("share.txt", b"shared bytes", "text/plain")})
    assert upload.status_code == 201
    document_id = upload.json()["id"]

    assert client.get(f"/api/v1/documents/{document_id}", headers=recipient_auth).status_code == 404
    shared = client.put(f"/api/v1/documents/{document_id}/shares", headers=owner_auth,
                        json={"email": "recipient@example.com", "permission": "read"})
    assert shared.status_code == 200
    assert client.get(f"/api/v1/documents/{document_id}", headers=recipient_auth).status_code == 200
    listing = client.get("/api/v1/documents", headers=recipient_auth)
    assert listing.json()["total"] == 1
    assert client.get(f"/api/v1/documents/{document_id}/download", headers=recipient_auth).status_code == 404
    assert client.delete(f"/api/v1/documents/{document_id}", headers=recipient_auth).status_code == 404

    assert client.put(f"/api/v1/documents/{document_id}/shares", headers=owner_auth,
                      json={"email": "recipient@example.com", "permission": "download"}).status_code == 200
    download = client.get(f"/api/v1/documents/{document_id}/download", headers=recipient_auth)
    assert download.status_code == 200
    assert download.content == b"shared bytes"
    assert client.delete(f"/api/v1/documents/{document_id}/shares/2", headers=owner_auth).status_code == 204
    assert client.get(f"/api/v1/documents/{document_id}", headers=recipient_auth).status_code == 404


def test_viewer_cannot_upload(client, monkeypatch):
    token = _register(client, "viewer@example.com")
    from app.api.deps import get_current_user
    from app.models import User

    def viewer():
        return User(id=1, email="viewer@example.com", full_name="Viewer", hashed_password="x", role="viewer")

    client.app.dependency_overrides[get_current_user] = viewer
    try:
        response = client.post("/api/v1/documents", headers={"Authorization": f"Bearer {token}"},
                               files={"file": ("notes.txt", b"text", "text/plain")})
        assert response.status_code == 403
    finally:
        client.app.dependency_overrides.pop(get_current_user, None)
