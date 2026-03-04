"""
Shared pytest fixtures for ScriptMesh tests.

Both the orchestrator and agent validate their API keys at import time, so
we must set the required environment variables *before* importing them.
The module-scoped autouse fixture below guarantees this for every test file.
"""

import os
import json
import pytest
import tempfile

# ---------------------------------------------------------------------------
# Environment must be configured before any ScriptMesh module is imported
# ---------------------------------------------------------------------------

TEST_MAIN_KEY = "test-orchestrator-key-minimum-32-chars-ok"
TEST_AGENT_KEY = "test-agent-secret-key-minimum-32chars"

os.environ.setdefault("SCRIPT_MESH_MAIN_KEY", TEST_MAIN_KEY)
os.environ.setdefault("SCRIPT_MESH_AGENT_KEY", TEST_AGENT_KEY)
os.environ.setdefault("ORCHESTRATOR_URL", "http://localhost:8000")


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """A temporary directory that acts as /data for file-read tests."""
    return tmp_path


@pytest.fixture()
def sample_script(tmp_path) -> str:
    """Write a trivial hello-world script and return its path as a string."""
    script = tmp_path / "hello.py"
    script.write_text('print("hello world")\n')
    return str(script)


@pytest.fixture()
def sample_manifest(tmp_path, sample_script) -> str:
    """Write a minimal script manifest and return the path."""
    manifest = {
        "scripts": [
            {"name": "hello", "path": sample_script},
        ]
    }
    manifest_path = tmp_path / "script_manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    return str(manifest_path)
