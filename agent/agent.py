from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from pathlib import Path
import logging
import json
import subprocess
import os
import sys
import re
import secrets
from datetime import datetime, timezone
import gzip
import shutil
import socket
import requests
import uvicorn
import time

# ---------------------------------------------------------------------------
# Directory & path setup
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

SCRIPTS_DIR = BASE_DIR / "scripts"
CFG_DIR = BASE_DIR / "cfg"
LOG_DIR = BASE_DIR / "logs"
MANIFEST_PATH = CFG_DIR / "script_manifest.json"

for _d in [SCRIPTS_DIR, CFG_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Configuration — fail fast on bad/default credentials
# ---------------------------------------------------------------------------

AGENT_API_KEY = os.getenv("SCRIPT_MESH_AGENT_KEY", "")
if not AGENT_API_KEY or AGENT_API_KEY == "localagent1secret":
    raise RuntimeError(
        "SCRIPT_MESH_AGENT_KEY environment variable must be set to a strong, "
        "unique secret. The default 'localagent1secret' value is not permitted. "
        "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
SCRIPT_MESH_MAIN_KEY = os.getenv("SCRIPT_MESH_MAIN_KEY", "")

SCRIPT_TIMEOUT_SECONDS = int(os.getenv("SCRIPT_TIMEOUT", "60"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def compress_old_logs(log_dir: Path, days_threshold: int = 7) -> None:
    now = datetime.now()
    compressed_count = 0
    for log_file in log_dir.glob("ScriptMesh-agent-*.log"):
        if log_file.suffix == ".gz":
            continue
        modified_time = datetime.fromtimestamp(log_file.stat().st_mtime)
        if (now - modified_time).days >= days_threshold:
            gz_path = log_file.with_suffix(log_file.suffix + ".gz")
            with log_file.open("rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            log_file.unlink()
            compressed_count += 1
    if compressed_count > 0:
        print(f"Compressed {compressed_count} old log file(s)")


compress_old_logs(LOG_DIR)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.propagate = False
if logger.hasHandlers():
    logger.handlers.clear()

_formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.FileHandler(LOG_DIR / "ScriptMesh-agent.log")
_daily_handler = logging.FileHandler(
    LOG_DIR / f"ScriptMesh-agent-{datetime.now().strftime('%Y-%m-%d')}.log"
)
_console_handler = logging.StreamHandler(sys.stdout)

for _h in [_file_handler, _daily_handler, _console_handler]:
    _h.setFormatter(_formatter)
    logger.addHandler(_h)

logger.info("ScriptMesh agent starting up")

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ScriptMesh Agent",
    description="Secure remote script execution agent",
    version="0.3.0",
)

# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

_OPEN_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}
_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def _verify_agent_key(request: Request, call_next):
    path = request.url.path.rstrip("/") or "/"
    if path not in _OPEN_PATHS:
        provided = request.headers.get("x-api-key", "")
        # Constant-time comparison prevents timing attacks
        if not secrets.compare_digest(provided, AGENT_API_KEY):
            # NOTE: Never log the provided key — it may contain secrets
            client = request.client.host if request.client else "unknown"
            logger.warning(
                "Unauthorized request to %s from %s — invalid or missing API key",
                request.url.path,
                client,
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: invalid or missing API key"},
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest not found at {MANIFEST_PATH}")
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def get_script_entry(name: str) -> dict | None:
    try:
        manifest = load_manifest()
        for entry in manifest.get("scripts", []):
            if entry["name"] == name:
                return entry
    except Exception as exc:
        logger.warning("Failed to read manifest for script lookup: %s", exc)
    return None


def get_hostname() -> str:
    return socket.gethostname()


def get_uptime() -> str:
    try:
        with open("/proc/uptime") as f:
            seconds = float(f.readline().split()[0])
        hours, remainder = divmod(int(seconds), 3600)
        minutes = remainder // 60
        return f"{hours}h {minutes}m"
    except Exception:
        return "unknown"


def _resolve_script_path(entry: dict) -> Path:
    """
    Resolve the script path from a manifest entry.

    Security: the resolved path *must* remain inside SCRIPTS_DIR to prevent
    directory-traversal attacks via crafted manifest entries.
    """
    raw = Path(entry["path"])
    if raw.is_absolute():
        resolved = raw.resolve()
    else:
        resolved = (SCRIPTS_DIR / raw).resolve()

    # Guard: ensure the resolved path stays within SCRIPTS_DIR
    try:
        resolved.relative_to(SCRIPTS_DIR.resolve())
    except ValueError:
        raise HTTPException(
            status_code=403,
            detail="Script path escapes the scripts directory — execution denied",
        )
    return resolved


# ---------------------------------------------------------------------------
# Orchestrator registration
# ---------------------------------------------------------------------------


def register_with_orchestrator(retries: int = 5, delay: int = 3) -> None:
    agent_name = f"{get_hostname()}_ScriptMesh_Agent"
    agent_url = os.getenv(
        "AGENT_URL",
        f"http://{socket.gethostbyname(socket.gethostname())}:5001",
    )
    payload = {
        "agent_name": agent_name,
        "url": agent_url,
        "api_key": AGENT_API_KEY,
    }
    headers = {"x-api-key": SCRIPT_MESH_MAIN_KEY}

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{ORCHESTRATOR_URL}/register-agent",
                json=payload,
                headers=headers,
                timeout=5,
            )
            resp.raise_for_status()
            logger.info("Registered with orchestrator: %s", resp.json())
            return
        except Exception as exc:
            logger.warning(
                "Registration attempt %d/%d failed: %s", attempt, retries, exc
            )
            if attempt < retries:
                time.sleep(delay)

    logger.error("All registration attempts failed — agent is running unregistered")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class RunScript(BaseModel):
    script_name: str

    @field_validator("script_name")
    @classmethod
    def _validate_script_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "script_name must be 1–64 alphanumeric characters, hyphens, or underscores"
            )
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def root():
    return {"service": "ScriptMesh Agent", "status": "running"}


@app.get("/heartbeat")
def heartbeat():
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": f"{get_hostname()}_ScriptMesh_Agent",
        "uptime": get_uptime(),
    }


@app.get("/get-scripts")
def get_scripts():
    logger.info("GET /get-scripts")
    try:
        data = load_manifest()
        logger.info("Manifest loaded successfully (%d scripts)", len(data.get("scripts", [])))
        return data
    except FileNotFoundError:
        logger.warning("Manifest file not found at %s", MANIFEST_PATH)
        raise HTTPException(status_code=404, detail="Script manifest not found")
    except Exception:
        logger.exception("Unexpected error in /get-scripts")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/run-script")
def run_script(payload: RunScript):
    logger.info("POST /run-script | script=%s", payload.script_name)

    script_entry = get_script_entry(payload.script_name)
    if not script_entry:
        logger.warning("Script not in manifest: %s", payload.script_name)
        raise HTTPException(status_code=404, detail="Script not found in manifest")

    script_path = _resolve_script_path(script_entry)

    if not script_path.exists():
        logger.warning("Script file missing on disk: %s", script_path)
        raise HTTPException(status_code=404, detail="Script file missing on disk")

    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Script '%s' timed out after %ds", payload.script_name, SCRIPT_TIMEOUT_SECONDS
        )
        raise HTTPException(
            status_code=504,
            detail=f"Script timed out after {SCRIPT_TIMEOUT_SECONDS} seconds",
        )
    except Exception:
        logger.exception("Exception while running script '%s'", payload.script_name)
        raise HTTPException(status_code=500, detail="Internal server error")

    logger.info(
        "Executed script '%s' | returncode=%d", payload.script_name, result.returncode
    )
    if result.stderr:
        logger.warning("stderr from '%s': %s", payload.script_name, result.stderr.strip())

    if result.returncode != 0:
        return JSONResponse(
            status_code=500,
            content={
                "error": "Script execution failed",
                "script": payload.script_name,
                "stderr": result.stderr.strip(),
                "returncode": result.returncode,
            },
        )

    return {
        "status": "success",
        "script": payload.script_name,
        "output": {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode,
        },
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    register_with_orchestrator()
    uvicorn.run(
        f"{Path(__file__).stem}:app",
        host="0.0.0.0",
        port=5001,
        reload=False,  # Never use reload=True in production
    )
