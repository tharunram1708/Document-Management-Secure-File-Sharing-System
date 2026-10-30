def test_validation_errors_use_the_documented_error_envelope(client):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "full_name": " ", "password": "short"},
        headers={"X-Request-ID": "contract-test-123"},
    )

    assert response.status_code == 422
    assert response.headers["X-Request-ID"] == "contract-test-123"
    body = response.json()
    assert body["error"]["code"] == 422
    assert body["error"]["message"] == "Request validation failed"
    assert body["error"]["details"]
    assert body["error"]["request_id"] == "contract-test-123"


def test_invalid_document_query_and_missing_resource_are_rejected(client):
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "reader@example.com", "full_name": "Reader", "password": "correct-horse-123"},
    )
    assert registration.status_code == 201
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "reader@example.com", "password": "correct-horse-123"},
    )
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

    invalid_query = client.get("/api/v1/documents?page=0", headers=auth)
    assert invalid_query.status_code == 422
    assert invalid_query.json()["error"]["code"] == 422
    missing = client.get("/api/v1/documents/9999", headers=auth)
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "Document not found"


def test_openapi_documents_routes_authentication_and_error_responses(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/auth/register" in paths
    assert "/api/v1/documents" in paths
    assert "/api/v1/documents/bulk" in paths
    assert "/api/v1/documents/{document_id}/shares" in paths
    assert "multipart/form-data" in paths["/api/v1/documents"]["post"]["requestBody"]["content"]
    assert paths["/api/v1/documents"]["post"]["security"] == [{"HTTPBearer": []}]
    assert "422" in paths["/api/v1/auth/register"]["post"]["responses"]
    assert "HTTPBearer" in schema["components"]["securitySchemes"]
