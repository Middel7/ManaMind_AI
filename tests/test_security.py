"""Tests de sécurité : fichiers sensibles, traversal, upload, headers."""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_static_blocks_env_file(client: TestClient):
    """La route catch-all doit bloquer .env (nom de fichier commençant par .)."""
    r = client.get("/.env")
    assert r.status_code == 404, (
        f"Attendu 404 pour /.env, obtenu {r.status_code}"
    )


def test_static_blocks_pyproject(client: TestClient):
    """La route catch-all doit bloquer pyproject.toml (dans _BLOCKED_NAMES)."""
    r = client.get("/pyproject.toml")
    assert r.status_code == 404, (
        f"Attendu 404 pour /pyproject.toml, obtenu {r.status_code}"
    )


def test_static_blocks_alembic_ini(client: TestClient):
    """La route catch-all doit bloquer alembic.ini (dans _BLOCKED_NAMES)."""
    r = client.get("/alembic.ini")
    assert r.status_code == 404, (
        f"Attendu 404 pour /alembic.ini, obtenu {r.status_code}"
    )


def test_static_blocks_env_example(client: TestClient):
    """La route catch-all doit bloquer .env.example."""
    r = client.get("/.env.example")
    assert r.status_code == 404, (
        f"Attendu 404 pour /.env.example, obtenu {r.status_code}"
    )


def test_static_blocks_path_traversal(client: TestClient):
    """Tentative de path traversal → 400 ou 404 (jamais 200)."""
    r = client.get("/../../../etc/passwd")
    assert r.status_code in (400, 404), (
        f"Attendu 400/404 pour path traversal, obtenu {r.status_code}"
    )


def test_upload_rejects_non_txt(client: TestClient):
    """Upload d'un fichier non-.txt → 400 (extension refusée)."""
    r = client.post(
        "/upload-deck",
        files={"deckfile": ("malicious.php", b"<?php echo 'hack'; ?>", "application/x-php")},
        data={"algo": "v1"},
    )
    # Sans auth (certaines routes) ou mauvais type (400) — jamais 200
    assert r.status_code in (400, 401, 422), (
        f"Attendu 400/401/422 pour upload non-.txt, obtenu {r.status_code}: {r.text}"
    )


def test_upload_rejects_exe(client: TestClient):
    """Upload d'un .exe → 400."""
    r = client.post(
        "/upload-deck",
        files={"deckfile": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"algo": "v1"},
    )
    assert r.status_code in (400, 401, 422), (
        f"Attendu 400/401/422 pour upload .exe, obtenu {r.status_code}: {r.text}"
    )


def test_health_endpoint(client: TestClient):
    """GET /health répond (200 ou 503 selon disponibilité DB) et contient status + db."""
    r = client.get("/health")
    assert r.status_code in (200, 503), (
        f"Attendu 200 ou 503, obtenu {r.status_code}: {r.text}"
    )
    data = r.json()
    assert "status" in data, f"Champ 'status' manquant dans {data}"
    assert "db" in data, f"Champ 'db' manquant dans {data}"


def test_security_headers_x_content_type(client: TestClient):
    """Le header X-Content-Type-Options: nosniff est présent sur toutes les réponses."""
    r = client.get("/health")
    assert r.headers.get("x-content-type-options") == "nosniff", (
        f"X-Content-Type-Options manquant ou incorrect: {dict(r.headers)}"
    )


def test_security_headers_x_frame_options(client: TestClient):
    """Le header X-Frame-Options: DENY est présent sur toutes les réponses."""
    r = client.get("/health")
    assert r.headers.get("x-frame-options") == "DENY", (
        f"X-Frame-Options manquant ou incorrect: {dict(r.headers)}"
    )


def test_security_headers_on_auth_route(client: TestClient):
    """Les headers de sécurité sont aussi présents sur les routes d'auth."""
    r = client.get("/auth/me")
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"


def test_rate_limit_login(client: TestClient):
    """Après plus de 10 tentatives de login en rafale → au moins une réponse 429."""
    responses = []
    for _ in range(12):
        r = client.post(
            "/auth/login",
            json={"email": "attacker@evil.com", "password": "wrongpass"},
        )
        responses.append(r.status_code)
    # Au moins une réponse 429 (rate limit slowapi : 10/minute)
    # Note : en CI sans Redis, slowapi utilise le compteur en mémoire → doit fonctionner.
    assert 429 in responses, (
        f"Attendu au moins un 429 parmi les réponses, obtenu : {responses}"
    )
