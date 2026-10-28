def test_register_login_and_get_current_user(client):
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "Alex@example.com", "full_name": "Alex Morgan", "password": "correct-horse-123"},
    )
    assert registration.status_code == 201
    assert registration.json()["email"] == "alex@example.com"
    assert "hashed_password" not in registration.json()

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "alex@example.com", "password": "correct-horse-123"},
    )
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"

    profile = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == "alex@example.com"


def test_duplicate_registration_and_bad_password_are_rejected(client):
    payload = {"email": "alex@example.com", "full_name": "Alex", "password": "correct-horse-123"}
    assert client.post("/api/v1/auth/register", json=payload).status_code == 201
    assert client.post("/api/v1/auth/register", json=payload).status_code == 409
    assert client.post(
        "/api/v1/auth/login",
        json={"email": "alex@example.com", "password": "wrong-password"},
    ).status_code == 401


def test_current_user_requires_a_valid_token(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get(
        "/api/v1/auth/me",
        headers={"Authorization": "Bearer not-a-jwt"},
    ).status_code == 401


def test_only_admin_can_assign_roles_and_registration_cannot_choose_role(client):
    registration = client.post(
        "/api/v1/auth/register",
        json={"email": "new@example.com", "full_name": "New User", "password": "correct-horse-123", "role": "admin"},
    )
    assert registration.status_code == 201
    assert registration.json()["role"] == "user"
    assert client.patch("/api/v1/auth/users/1/role", json={"role": "admin"}).status_code == 401

    from app.api.deps import get_current_user
    from app.models import User

    def admin():
        return User(id=999, email="admin@example.com", full_name="Admin", hashed_password="x", role="admin")

    client.app.dependency_overrides[get_current_user] = admin
    try:
        changed = client.patch("/api/v1/auth/users/1/role", json={"role": "manager"})
        assert changed.status_code == 200
        assert changed.json()["role"] == "manager"
    finally:
        client.app.dependency_overrides.pop(get_current_user, None)
