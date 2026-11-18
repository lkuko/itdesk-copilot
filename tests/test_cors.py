"""Browser-origin policy and its explicit local configuration; no provider calls."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app


@pytest.fixture
def cors_client(settings, provider):
    with TestClient(create_app(settings, provider)) as client:
        yield client
    assert provider.queries == provider.document_batches == provider.generation_calls == []


@pytest.mark.parametrize("origin", ["http://localhost:5173", "http://127.0.0.1:5173"])
def test_default_frontend_origins_receive_read_and_preflight_headers(cors_client, origin):
    response = cors_client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in response.headers
    preflight = cors_client.options("/api/sessions", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == origin
    assert set(preflight.headers["access-control-allow-methods"].split(", ")) == {
        "GET", "POST", "PATCH", "DELETE",
    }


@pytest.mark.parametrize("origin", ["https://untrusted.example", "http://localhost:5174", "null"])
def test_untrusted_browser_origins_get_no_cors_read_permission(cors_client, origin):
    response = cors_client.get("/api/health", headers={"Origin": origin})
    assert response.status_code == 200  # CORS does not authenticate or reject ordinary HTTP clients.
    assert "access-control-allow-origin" not in response.headers
    preflight = cors_client.options("/api/sessions", headers={
        "Origin": origin, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    })
    assert preflight.status_code == 400
    assert "access-control-allow-origin" not in preflight.headers


@pytest.mark.parametrize("method,headers", [("PUT", "content-type"), ("POST", "authorization")])
def test_unsupported_preflight_methods_or_headers_are_denied(cors_client, method, headers):
    response = cors_client.options("/api/sessions", headers={
        "Origin": "http://localhost:5173", "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": headers,
    })
    assert response.status_code == 400


def test_requests_without_origin_remain_compatible_and_errors_keep_cors(cors_client):
    plain = cors_client.get("/api/health")
    assert plain.status_code == 200
    assert "access-control-allow-origin" not in plain.headers
    invalid = cors_client.post("/api/sessions", json={"unexpected": True}, headers={
        "Origin": "http://localhost:5173",
    })
    assert invalid.status_code == 422
    assert invalid.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_environment_json_array_replaces_default_origins(settings, provider, monkeypatch):
    monkeypatch.setenv("ITDESK_CORS_ORIGINS", '["https://demo.example.test/","https://demo.example.test"]')
    configured = Settings(_env_file=None)
    assert configured.cors_origins == ["https://demo.example.test"]
    local = settings.model_copy(update={"cors_origins": configured.cors_origins})
    with TestClient(create_app(local, provider)) as client:
        accepted = client.get("/api/health", headers={"Origin": "https://demo.example.test"})
        rejected = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert accepted.headers["access-control-allow-origin"] == "https://demo.example.test"
    assert "access-control-allow-origin" not in rejected.headers


def test_empty_origin_list_disables_cross_origin_access(settings, provider):
    local = settings.model_copy(update={"cors_origins": []})
    with TestClient(create_app(local, provider)) as client:
        response = client.options("/api/sessions", headers={
            "Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
        })
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("origin", [
    "*", "https://*.example.test", "https://user:pass@example.test", "https://example.test/api",
    "https://example.test?key=value", "https://example.test#fragment", "file://localhost",
    "https://example.test:99999", "https://example.test\n",
])
def test_invalid_or_credential_bearing_origin_is_rejected(origin):
    with pytest.raises(ValidationError, match="CORS origins must be explicit"):
        Settings(_env_file=None, cors_origins=[origin])
