"""
Tests for the ScriptMesh agent.

Covers:
- Authentication middleware (valid key, invalid key, missing key)
- Security headers on every response
- /heartbeat endpoint (no auth required)
- /get-scripts — manifest found / not found
- /run-script — happy path, script not in manifest, path traversal in manifest,
  script timeout, invalid script name format
- _resolve_script_path path-traversal guard
"""

import json
import os
import subprocess
import pytest

from pathlib import Path
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# conftest sets SCRIPT_MESH_AGENT_KEY before this import
from agent.agent import app, SCRIPTS_DIR, _resolve_script_path, MANIFEST_PATH

VALID_KEY = os.environ["SCRIPT_MESH_AGENT_KEY"]
BAD_KEY = "totally-wrong-key"

client = TestClient(app, raise_server_exceptions=False)


def auth(key: str = VALID_KEY) -> dict:
    return {"x-api-key": key}


# ---------------------------------------------------------------------------
# Root / heartbeat — no auth required
# ---------------------------------------------------------------------------


def test_root_no_auth():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "ScriptMesh Agent"


def test_heartbeat_requires_auth():
    """Heartbeat is an authenticated endpoint — the orchestrator always passes its key."""
    r = client.get("/heartbeat")
    assert r.status_code == 401


def test_heartbeat_with_valid_auth():
    r = client.get("/heartbeat", headers=auth())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "alive"
    assert "timestamp" in data
    assert "agent" in data
    assert "uptime" in data


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


def test_security_headers_on_heartbeat():
    r = client.get("/heartbeat", headers=auth())
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Cache-Control") == "no-store"


def test_security_headers_on_auth_route():
    r = client.get("/get-scripts", headers=auth())
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_auth_valid_key():
    r = client.get("/get-scripts", headers=auth(VALID_KEY))
    # May be 200 or 404 (no manifest) — just must not be 401
    assert r.status_code != 401


def test_auth_missing_key():
    r = client.get("/get-scripts")
    assert r.status_code == 401
    assert "Unauthorized" in r.json()["detail"]


def test_auth_wrong_key():
    r = client.get("/get-scripts", headers=auth(BAD_KEY))
    assert r.status_code == 401


def test_auth_empty_key():
    r = client.get("/get-scripts", headers={"x-api-key": ""})
    assert r.status_code == 401


def test_run_script_requires_auth():
    r = client.post("/run-script", json={"script_name": "hello"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# /get-scripts
# ---------------------------------------------------------------------------


def test_get_scripts_manifest_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("agent.agent.MANIFEST_PATH", tmp_path / "missing.json")
    r = client.get("/get-scripts", headers=auth())
    assert r.status_code == 404


def test_get_scripts_returns_manifest(monkeypatch, tmp_path):
    manifest = {"scripts": [{"name": "hello", "path": "hello.py"}]}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))
    monkeypatch.setattr("agent.agent.MANIFEST_PATH", mp)

    r = client.get("/get-scripts", headers=auth())
    assert r.status_code == 200
    data = r.json()
    assert data["scripts"][0]["name"] == "hello"


# ---------------------------------------------------------------------------
# /run-script — input validation
# ---------------------------------------------------------------------------


def test_run_script_invalid_name_special_chars():
    r = client.post("/run-script", json={"script_name": "bad/name"}, headers=auth())
    assert r.status_code == 422


def test_run_script_invalid_name_too_long():
    r = client.post(
        "/run-script", json={"script_name": "a" * 65}, headers=auth()
    )
    assert r.status_code == 422


def test_run_script_not_in_manifest(monkeypatch, tmp_path):
    manifest = {"scripts": [{"name": "other", "path": "other.py"}]}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))
    monkeypatch.setattr("agent.agent.MANIFEST_PATH", mp)

    r = client.post("/run-script", json={"script_name": "hello"}, headers=auth())
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /run-script — happy path
# ---------------------------------------------------------------------------


def test_run_script_success(monkeypatch, tmp_path):
    # Create a real script
    script_file = tmp_path / "hello.py"
    script_file.write_text('print("hello world")\n')

    manifest = {"scripts": [{"name": "hello", "path": str(script_file)}]}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))

    monkeypatch.setattr("agent.agent.MANIFEST_PATH", mp)
    monkeypatch.setattr("agent.agent.SCRIPTS_DIR", tmp_path)

    r = client.post("/run-script", json={"script_name": "hello"}, headers=auth())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "success"
    assert "hello world" in data["output"]["stdout"]
    assert data["output"]["returncode"] == 0


def test_run_script_nonzero_exit(monkeypatch, tmp_path):
    script_file = tmp_path / "fail.py"
    script_file.write_text("import sys; sys.exit(1)\n")

    manifest = {"scripts": [{"name": "fail", "path": str(script_file)}]}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))

    monkeypatch.setattr("agent.agent.MANIFEST_PATH", mp)
    monkeypatch.setattr("agent.agent.SCRIPTS_DIR", tmp_path)

    r = client.post("/run-script", json={"script_name": "fail"}, headers=auth())
    assert r.status_code == 500
    assert r.json()["returncode"] == 1


# ---------------------------------------------------------------------------
# /run-script — timeout
# ---------------------------------------------------------------------------


def test_run_script_timeout(monkeypatch, tmp_path):
    script_file = tmp_path / "sleep.py"
    script_file.write_text("import time; time.sleep(999)\n")

    manifest = {"scripts": [{"name": "sleep", "path": str(script_file)}]}
    mp = tmp_path / "manifest.json"
    mp.write_text(json.dumps(manifest))

    monkeypatch.setattr("agent.agent.MANIFEST_PATH", mp)
    monkeypatch.setattr("agent.agent.SCRIPTS_DIR", tmp_path)
    monkeypatch.setattr("agent.agent.SCRIPT_TIMEOUT_SECONDS", 1)

    r = client.post("/run-script", json={"script_name": "sleep"}, headers=auth())
    assert r.status_code == 504
    assert "timed out" in r.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Path traversal — _resolve_script_path guard
# ---------------------------------------------------------------------------


def test_resolve_path_normal(tmp_path):
    """A relative path within SCRIPTS_DIR resolves correctly."""
    (tmp_path / "safe.py").touch()
    import agent.agent as ag

    original_scripts_dir = ag.SCRIPTS_DIR
    ag.SCRIPTS_DIR = tmp_path
    try:
        entry = {"path": "safe.py"}
        resolved = _resolve_script_path(entry)
        assert resolved == (tmp_path / "safe.py").resolve()
    finally:
        ag.SCRIPTS_DIR = original_scripts_dir


def test_resolve_path_traversal_blocked(tmp_path, monkeypatch):
    """A path that escapes SCRIPTS_DIR must be rejected with 403."""
    import agent.agent as ag
    from fastapi import HTTPException

    monkeypatch.setattr(ag, "SCRIPTS_DIR", tmp_path / "scripts")
    (tmp_path / "scripts").mkdir()

    entry = {"path": "../../../etc/passwd"}
    with pytest.raises(HTTPException) as exc_info:
        _resolve_script_path(entry)
    assert exc_info.value.status_code == 403


def test_resolve_absolute_path_outside_scripts_dir_blocked(tmp_path, monkeypatch):
    """An absolute path outside SCRIPTS_DIR must be rejected."""
    import agent.agent as ag
    from fastapi import HTTPException

    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setattr(ag, "SCRIPTS_DIR", scripts_dir)

    entry = {"path": "/etc/passwd"}
    with pytest.raises(HTTPException) as exc_info:
        _resolve_script_path(entry)
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Auth key never leaks into logs
# ---------------------------------------------------------------------------


def test_auth_key_not_in_log_output(monkeypatch, caplog):
    """The agent must never log API key values."""
    import logging

    with caplog.at_level(logging.INFO, logger="agent.agent"):
        client.get("/get-scripts", headers=auth(BAD_KEY))

    for record in caplog.records:
        assert VALID_KEY not in record.getMessage()
        assert BAD_KEY not in record.getMessage()
