"""
Tests for the ScriptMesh orchestrator.

Covers:
- Authentication middleware (valid key, invalid key, missing key)
- Security headers on every response
- Request-ID header present on every response
- Input validation (agent name, URL, api_key length, script name)
- Path traversal protection on /read
- /health endpoint availability without auth
- /register-agent happy path and validation failures
- /get-agents, /agent-status endpoints
- /trigger-script and /get-scripts error paths
- Encryption round-trip for the registry
"""

import json
import os
import pytest

from fastapi.testclient import TestClient

# conftest sets env vars; import the app after that
from orchestrator.orchestrator import app, registered_agents, _save_registry, _load_registry

VALID_KEY = os.environ["SCRIPT_MESH_MAIN_KEY"]
BAD_KEY = "definitely-wrong-key"

client = TestClient(app, raise_server_exceptions=False)


def auth(key: str = VALID_KEY) -> dict:
    return {"x-api-key": key}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_dummy_agent(name: str = "test_agent") -> dict:
    payload = {
        "agent_name": name,
        "url": "http://127.0.0.1:5001",
        "api_key": "a-valid-agent-secret-key-here",
    }
    return client.post("/register-agent", json=payload, headers=auth())


# ---------------------------------------------------------------------------
# Root / health — no auth required
# ---------------------------------------------------------------------------


def test_root_no_auth():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "ScriptMesh Orchestrator"


def test_health_no_auth():
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "orchestrator_alive"
    assert "uptime_seconds" in data
    assert "registered_agents" in data
    assert "version" in data


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_on_health():
    r = client.get("/health")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Cache-Control") == "no-store"


def test_security_headers_on_auth_route():
    r = client.get("/get-agents", headers=auth())
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_request_id_header_present():
    r = client.get("/health")
    assert "X-Request-ID" in r.headers
    # Should be a non-empty string (UUID format)
    assert len(r.headers["X-Request-ID"]) == 36


# ---------------------------------------------------------------------------
# Authentication middleware
# ---------------------------------------------------------------------------


def test_auth_valid_key():
    r = client.get("/get-agents", headers=auth(VALID_KEY))
    assert r.status_code == 200


def test_auth_missing_key():
    r = client.get("/get-agents")
    assert r.status_code == 401
    assert "Unauthorized" in r.json()["detail"]


def test_auth_wrong_key():
    r = client.get("/get-agents", headers=auth(BAD_KEY))
    assert r.status_code == 401


def test_auth_empty_key():
    r = client.get("/get-agents", headers={"x-api-key": ""})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /register-agent
# ---------------------------------------------------------------------------


def test_register_agent_success():
    r = _register_dummy_agent("reg_test_agent")
    assert r.status_code == 200
    assert r.json()["agent"] == "reg_test_agent"


def test_register_agent_invalid_name_special_chars():
    payload = {
        "agent_name": "bad name!",
        "url": "http://127.0.0.1:5001",
        "api_key": "a-valid-agent-secret-key-here",
    }
    r = client.post("/register-agent", json=payload, headers=auth())
    assert r.status_code == 422


def test_register_agent_name_too_long():
    payload = {
        "agent_name": "a" * 65,
        "url": "http://127.0.0.1:5001",
        "api_key": "a-valid-agent-secret-key-here",
    }
    r = client.post("/register-agent", json=payload, headers=auth())
    assert r.status_code == 422


def test_register_agent_invalid_url_scheme():
    payload = {
        "agent_name": "agent1",
        "url": "ftp://bad-scheme",
        "api_key": "a-valid-agent-secret-key-here",
    }
    r = client.post("/register-agent", json=payload, headers=auth())
    assert r.status_code == 422


def test_register_agent_api_key_too_short():
    payload = {
        "agent_name": "agent2",
        "url": "http://127.0.0.1:5001",
        "api_key": "short",
    }
    r = client.post("/register-agent", json=payload, headers=auth())
    assert r.status_code == 422


def test_register_agent_unauthenticated():
    payload = {
        "agent_name": "agent3",
        "url": "http://127.0.0.1:5001",
        "api_key": "a-valid-agent-secret-key-here",
    }
    r = client.post("/register-agent", json=payload)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /get-agents
# ---------------------------------------------------------------------------


def test_get_agents_returns_dict():
    r = client.get("/get-agents", headers=auth())
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_get_agents_does_not_expose_api_keys():
    _register_dummy_agent("key_leak_check")
    r = client.get("/get-agents", headers=auth())
    data = r.json()
    for agent_data in data.values():
        assert "api_key" not in agent_data


# ---------------------------------------------------------------------------
# /agent-status
# ---------------------------------------------------------------------------


def test_agent_status_structure():
    _register_dummy_agent("status_agent")
    r = client.get("/agent-status", headers=auth())
    assert r.status_code == 200
    data = r.json()
    for info in data.values():
        assert "url" in info
        assert "status" in info
        assert "api_key" not in info  # Must never expose keys


# ---------------------------------------------------------------------------
# /get-scripts — agent not found
# ---------------------------------------------------------------------------


def test_get_scripts_unknown_agent():
    r = client.get("/get-scripts", params={"agent": "nonexistent"}, headers=auth())
    assert r.status_code == 404


def test_get_scripts_invalid_agent_name():
    r = client.get("/get-scripts", params={"agent": "bad name!"}, headers=auth())
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /trigger-script — validation
# ---------------------------------------------------------------------------


def test_trigger_script_invalid_agent_name():
    payload = {"run_script": "hello", "agent": "bad name!"}
    r = client.post("/trigger-script", json=payload, headers=auth())
    assert r.status_code == 422


def test_trigger_script_invalid_script_name():
    payload = {"run_script": "bad/script", "agent": "agent1"}
    r = client.post("/trigger-script", json=payload, headers=auth())
    assert r.status_code == 422


def test_trigger_script_unknown_agent():
    payload = {"run_script": "hello", "agent": "unknown_agent"}
    r = client.post("/trigger-script", json=payload, headers=auth())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /read — path traversal protection
# ---------------------------------------------------------------------------


def test_read_path_traversal_blocked():
    """../etc/passwd must be blocked even though it looks like a valid filename."""
    r = client.get("/read", params={"filename": "../etc/passwd"}, headers=auth())
    assert r.status_code in (400, 404)


def test_read_path_traversal_blocked_absolute():
    r = client.get("/read", params={"filename": "/etc/passwd"}, headers=auth())
    assert r.status_code in (400, 404)


def test_read_nonexistent_file():
    r = client.get("/read", params={"filename": "no_such_file.txt"}, headers=auth())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Encryption round-trip
# ---------------------------------------------------------------------------


def test_encryption_round_trip():
    from orchestrator.orchestrator import encrypt_string, decrypt_string

    original = "super-secret-key-value-for-testing"
    encrypted = encrypt_string(original)
    assert encrypted != original
    assert decrypt_string(encrypted) == original


# ---------------------------------------------------------------------------
# Registry persistence
# ---------------------------------------------------------------------------


def test_registry_save_load_round_trip(tmp_path, monkeypatch):
    """Saved registry must decrypt back to the same plaintext keys."""
    from orchestrator import orchestrator as orch

    # Patch the registry path to a temp file
    monkeypatch.setattr(orch, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(
        orch,
        "registered_agents",
        {"myagent": {"url": "http://x:5001", "api_key": "plaintext-agent-key-32chars", "last_seen": "now"}},
    )

    orch._save_registry()

    # Verify the file contains an encrypted (not plaintext) key
    raw = json.loads((tmp_path / "registry.json").read_text())
    assert raw["myagent"]["api_key"] != "plaintext-agent-key-32chars"

    # Reload and verify decryption
    loaded = orch._load_registry()
    assert loaded["myagent"]["api_key"] == "plaintext-agent-key-32chars"
