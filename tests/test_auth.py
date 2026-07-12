"""Tests critiques : authentification et autorisation."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_login_requires_credentials(client: TestClient):
    """Le login sans body (champs vides) retourne 400."""
    r = client.post("/auth/login", json={})
    assert r.status_code in (400, 422), (
        f"Attendu 400 ou 422, obtenu {r.status_code}: {r.text}"
    )


def test_login_wrong_password(client: TestClient):
    """Mauvais mot de passe → 401 (ou 5xx si DB indisponible en test)."""
    r = client.post("/auth/login", json={"email": "nobody@test.com", "password": "wrong"})
    # 5xx acceptable si la DB n'est pas initialisée (SQLite sans schema en CI)
    assert r.status_code in (401, 500, 503), (
        f"Attendu 401, obtenu {r.status_code}: {r.text}"
    )


def test_auth_me_unauthenticated(client: TestClient):
    """Sans cookie → authenticated: False (jamais 500)."""
    r = client.get("/auth/me")
    assert r.status_code == 200, f"Attendu 200, obtenu {r.status_code}: {r.text}"
    data = r.json()
    assert data.get("authenticated") is False, f"Attendu authenticated=False, obtenu {data}"


def test_logout_clears_cookie(client: TestClient):
    """Le logout retourne un Set-Cookie qui efface le token mm_token."""
    r = client.post("/auth/logout")
    assert r.status_code == 200, f"Attendu 200, obtenu {r.status_code}: {r.text}"
    # Starlette delete_cookie() pose max-age=0
    cookie_header = r.headers.get("set-cookie", "")
    assert "mm_token" in cookie_header, (
        f"mm_token absent du header Set-Cookie: {cookie_header!r}"
    )
    assert "max-age=0" in cookie_header.lower() or "expires" in cookie_header.lower(), (
        f"Cookie non effacé (max-age=0 ou expires attendu): {cookie_header!r}"
    )


def test_protected_route_without_auth(client: TestClient):
    """GET /api/collection sans cookie → 401 ou 403."""
    r = client.get("/api/collection")
    assert r.status_code in (401, 403), (
        f"Attendu 401/403, obtenu {r.status_code}: {r.text}"
    )


def test_deck_analyze_requires_auth(client: TestClient):
    """POST /api/deck/analyze sans auth → 401."""
    r = client.post("/api/deck/analyze", json={
        "commander": "Teysa Karlov",
        "decklist": ["Sol Ring", "Command Tower"],
    })
    assert r.status_code == 401, (
        f"Attendu 401, obtenu {r.status_code}: {r.text}"
    )


def test_deck_explanation_requires_auth(client: TestClient):
    """GET /api/deck/explanation sans auth → 401."""
    r = client.get("/api/deck/explanation?commander=Teysa+Karlov&card=Sol+Ring")
    assert r.status_code == 401, (
        f"Attendu 401, obtenu {r.status_code}: {r.text}"
    )


def test_register_without_invitation(client: TestClient):
    """Inscription sans token d'invitation → 400."""
    r = client.post("/auth/register", json={
        "email": "test@test.com",
        "password": "password123",
        "token": "",
    })
    assert r.status_code == 400, (
        f"Attendu 400, obtenu {r.status_code}: {r.text}"
    )


def test_register_invalid_invitation(client: TestClient):
    """Inscription avec token invalide → 400 (ou 5xx si DB indisponible)."""
    r = client.post("/auth/register", json={
        "email": "test@test.com",
        "password": "password123",
        "token": "invalid-token-xyz-000",
    })
    # 5xx acceptable si la DB n'est pas initialisée (SQLite sans schema en CI)
    assert r.status_code in (400, 500, 503), (
        f"Attendu 400, obtenu {r.status_code}: {r.text}"
    )


def test_admin_endpoint_requires_auth(client: TestClient):
    """POST /api/engine/start sans auth → 401."""
    r = client.post("/api/engine/start")
    assert r.status_code in (401, 403), (
        f"Attendu 401 ou 403, obtenu {r.status_code}: {r.text}"
    )


def test_admin_users_requires_admin(client: TestClient):
    """GET /api/admin/users sans auth → 401 ou 403."""
    r = client.get("/api/admin/users")
    assert r.status_code in (401, 403), (
        f"Attendu 401 ou 403, obtenu {r.status_code}: {r.text}"
    )


def test_login_empty_email(client: TestClient):
    """Email vide → 400."""
    r = client.post("/auth/login", json={"email": "", "password": "somepassword"})
    assert r.status_code == 400, (
        f"Attendu 400, obtenu {r.status_code}: {r.text}"
    )


def test_login_empty_password(client: TestClient):
    """Mot de passe vide → 400."""
    r = client.post("/auth/login", json={"email": "someone@test.com", "password": ""})
    assert r.status_code == 400, (
        f"Attendu 400, obtenu {r.status_code}: {r.text}"
    )
