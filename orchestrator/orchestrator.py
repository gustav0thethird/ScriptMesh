from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from pathlib import Path
from datetime import datetime, timezone
import requests
import os
import logging
import sys
import gzip
import shutil
import json
import asyncio
import secrets
import re
import uuid
from contextlib import asynccontextmanager
from cryptography.fernet import Fernet
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ---------------------------------------------------------------------------
# Configuration — fail fast on bad/default credentials
# ---------------------------------------------------------------------------

SCRIPT_MESH_MAIN_KEY = os.getenv("SCRIPT_MESH_MAIN_KEY", "")
if not SCRIPT_MESH_MAIN_KEY or SCRIPT_MESH_MAIN_KEY == "CHANGEME":
    raise RuntimeError(
        "SCRIPT_MESH_MAIN_KEY environment variable must be set to a strong, "
        "unique secret. The default 'CHANGEME' value is not permitted in "
        "production. Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )

# Disable interactive API docs in production
DISABLE_DOCS = os.getenv("DISABLE_DOCS", "false").lower() == "true"

# Comma-separated allowed CORS origins (empty = CORS disabled)
_raw_origins = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = [o.strip() for o in _raw_origins.split(",") if o.strip()]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)


def compress_old_logs(log_dir: Path, days_threshold: int = 7) -> None:
    now = datetime.now()
    compressed_count = 0
    for log_file in log_dir.glob("ScriptMesh-orchestrator-*.log"):
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
_file_handler = logging.FileHandler(LOG_DIR / "ScriptMesh-orchestrator.log")
_daily_handler = logging.FileHandler(
    LOG_DIR / f"ScriptMesh-orchestrator-{datetime.now().strftime('%Y-%m-%d')}.log"
)
_console_handler = logging.StreamHandler(sys.stdout)

for _h in [_file_handler, _daily_handler, _console_handler]:
    _h.setFormatter(_formatter)
    logger.addHandler(_h)

logger.info("ScriptMesh orchestrator starting up")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])

# ---------------------------------------------------------------------------
# Encryption — Fernet keys stored on disk, plaintext in memory
# ---------------------------------------------------------------------------

CFG_DIR = Path("cfg")
CFG_DIR.mkdir(parents=True, exist_ok=True)

FERNET_KEY_PATH = CFG_DIR / "fernet.key"
if not FERNET_KEY_PATH.exists():
    FERNET_KEY_PATH.write_bytes(Fernet.generate_key())
    logger.info("Generated new Fernet encryption key at %s", FERNET_KEY_PATH)

_fernet_key = FERNET_KEY_PATH.read_bytes().strip()
_fernet = Fernet(_fernet_key)


def encrypt_string(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt_string(value: str) -> str:
    return _fernet.decrypt(value.encode().strip()).decode()


# ---------------------------------------------------------------------------
# Agent registry
# Invariant: registered_agents[name]["api_key"] is ALWAYS plaintext in memory.
# Keys are only encrypted when persisted to disk.
# ---------------------------------------------------------------------------

REGISTRY_PATH = CFG_DIR / "agent_registry.json"

# In-memory store: { agent_name: { url, api_key (plaintext), last_seen } }
registered_agents: dict = {}
agent_status_cache: dict = {}

_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def _save_registry() -> None:
    """Persist registry to disk with encrypted API keys."""
    data: dict = {}
    for name, info in registered_agents.items():
        data[name] = {
            "url": info["url"],
            "last_seen": info.get("last_seen"),
            "api_key": encrypt_string(info["api_key"]),
        }
    REGISTRY_PATH.write_text(json.dumps(data, indent=2))


def _load_registry() -> dict:
    """Load registry from disk, decrypting API keys into plaintext."""
    if not REGISTRY_PATH.exists():
        return {}
    try:
        with open(REGISTRY_PATH) as f:
            raw = json.load(f)
        result: dict = {}
        for name, info in raw.items():
            try:
                result[name] = {
                    "url": info["url"],
                    "last_seen": info.get("last_seen"),
                    "api_key": decrypt_string(info["api_key"]),
                }
            except Exception:
                logger.warning("Failed to decrypt registry entry for '%s' — skipping", name)
        return result
    except Exception:
        logger.exception("Failed to load agent registry from disk")
        return {}


registered_agents = _load_registry()

# ---------------------------------------------------------------------------
# Background health-check loop
# ---------------------------------------------------------------------------

HEALTH_CHECK_INTERVAL_SECONDS = int(os.getenv("HEALTH_CHECK_INTERVAL", "60"))


async def _agent_health_loop() -> None:
    while True:
        await asyncio.sleep(HEALTH_CHECK_INTERVAL_SECONDS)
        if not registered_agents:
            continue
        logger.info("[Healthcheck] Pinging %d registered agent(s)...", len(registered_agents))
        for name, info in list(registered_agents.items()):
            try:
                response = await asyncio.to_thread(
                    requests.get,
                    f"{info['url']}/heartbeat",
                    # Use the plaintext key directly — no decrypt needed
                    headers={"x-api-key": info["api_key"]},
                    timeout=5,
                )
                status = "online" if response.status_code == 200 else f"error_{response.status_code}"
            except Exception:
                status = "offline"
                logger.warning("[Healthcheck] Agent '%s' is offline or unreachable", name)
            else:
                logger.info("[Healthcheck] Agent '%s' → %s", name, status)

            agent_status_cache[name] = {
                "status": status,
                "last_checked": datetime.utcnow().isoformat(),
            }


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_agent_health_loop())
    logger.info("ScriptMesh orchestrator ready")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("ScriptMesh orchestrator shut down cleanly")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="ScriptMesh Orchestrator",
    description="Secure distributed script-execution orchestrator",
    version="0.3.0",
    lifespan=lifespan,
    docs_url=None if DISABLE_DOCS else "/docs",
    redoc_url=None if DISABLE_DOCS else "/redoc",
    openapi_url=None if DISABLE_DOCS else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

if ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=ALLOWED_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["x-api-key", "Content-Type"],
    )

_OPEN_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def _request_id(request: Request, call_next):
    rid = str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def _verify_key(request: Request, call_next):
    if request.url.path not in _OPEN_PATHS:
        key = request.headers.get("x-api-key", "")
        if not secrets.compare_digest(key, SCRIPT_MESH_MAIN_KEY):
            client = request.client.host if request.client else "unknown"
            logger.warning(
                "Unauthorized request to %s from %s", request.url.path, client
            )
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AgentRegistration(BaseModel):
    agent_name: str
    url: str
    api_key: str

    @field_validator("agent_name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "agent_name must be 1–64 alphanumeric characters, hyphens, or underscores"
            )
        return v

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        if len(v) > 256:
            raise ValueError("url is too long (max 256 chars)")
        return v

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, v: str) -> str:
        if len(v) < 16:
            raise ValueError("api_key must be at least 16 characters")
        return v


class RunScript(BaseModel):
    run_script: str
    agent: str

    @field_validator("run_script", "agent")
    @classmethod
    def _validate_identifier(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError(
                "Field must be 1–64 alphanumeric characters, hyphens, or underscores"
            )
        return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

_START_TIME = datetime.now(timezone.utc)


@app.get("/")
def root():
    return {"service": "ScriptMesh Orchestrator", "status": "running"}


@app.get("/health")
def healthcheck():
    return {
        "status": "orchestrator_alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int((datetime.now(timezone.utc) - _START_TIME).total_seconds()),
        "registered_agents": len(registered_agents),
        "version": "0.3.0",
    }


@app.post("/register-agent")
@limiter.limit("20/minute")
def register_agent(payload: AgentRegistration, request: Request):
    logger.info("Registering agent '%s' at %s", payload.agent_name, payload.url)
    now = datetime.utcnow().isoformat()

    # Store plaintext key in memory; _save_registry encrypts on disk
    registered_agents[payload.agent_name] = {
        "url": payload.url,
        "api_key": payload.api_key,
        "last_seen": now,
    }
    agent_status_cache[payload.agent_name] = {
        "status": "online",
        "last_checked": now,
    }
    _save_registry()

    return {"status": "registered", "agent": payload.agent_name}


@app.get("/agent-status")
def get_agent_status():
    return {
        name: {
            "url": info["url"],
            "last_seen": info.get("last_seen"),
            "status": agent_status_cache.get(name, {}).get("status", "unknown"),
            "last_checked": agent_status_cache.get(name, {}).get("last_checked"),
        }
        for name, info in registered_agents.items()
    }


@app.get("/get-agents")
def get_agents():
    logger.info("GET /get-agents")
    return {
        name: {"url": info["url"], "last_seen": info.get("last_seen")}
        for name, info in registered_agents.items()
    }


@app.get("/get-scripts")
def get_agent_scripts(agent: str = Query(..., min_length=1, max_length=64)):
    logger.info("GET /get-scripts | agent=%s", agent)
    if not _NAME_RE.match(agent):
        raise HTTPException(status_code=400, detail="Invalid agent name format")

    agent_info = registered_agents.get(agent)
    if not agent_info:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        response = requests.get(
            f"{agent_info['url']}/get-scripts",
            headers={"x-api-key": agent_info["api_key"]},
            timeout=10,
        )
        response.raise_for_status()
        logger.info("Fetched script list from agent '%s'", agent)
        return response.json()
    except requests.Timeout:
        logger.warning("Timeout while contacting agent '%s'", agent)
        raise HTTPException(status_code=504, detail="Agent timed out")
    except requests.HTTPError as exc:
        logger.warning("HTTP error from agent '%s': %s", agent, exc)
        raise HTTPException(status_code=502, detail="Agent returned an error")
    except Exception:
        logger.exception("Unexpected error contacting agent '%s'", agent)
        raise HTTPException(status_code=502, detail="Error communicating with agent")


@app.post("/trigger-script")
@limiter.limit("60/minute")
def trigger_agent_script(script: RunScript, request: Request):
    logger.info(
        "POST /trigger-script | agent=%s script=%s", script.agent, script.run_script
    )
    agent_info = registered_agents.get(script.agent)
    if not agent_info:
        raise HTTPException(status_code=404, detail="Agent not found")

    try:
        response = requests.post(
            f"{agent_info['url']}/run-script",
            json={"script_name": script.run_script},
            headers={"x-api-key": agent_info["api_key"]},
            timeout=120,  # scripts may be long-running
        )
        if response.status_code != 200:
            try:
                detail = response.json().get("detail", "Script execution failed")
            except Exception:
                detail = "Script execution failed"
            raise HTTPException(status_code=response.status_code, detail=detail)

        logger.info(
            "Script '%s' triggered successfully on agent '%s'",
            script.run_script,
            script.agent,
        )
        return {"status": "success", "agent": script.agent, "output": response.json()}

    except requests.Timeout:
        logger.warning("Timeout during script execution on agent '%s'", script.agent)
        raise HTTPException(status_code=504, detail="Agent timed out during script execution")
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unexpected error triggering script on agent '%s'", script.agent)
        raise HTTPException(status_code=502, detail="Error communicating with agent")


@app.get("/read")
def read_file(filename: str = Query(..., min_length=1, max_length=256)):
    logger.info("GET /read | filename=%s", filename)
    base_dir = Path("/data").resolve()
    try:
        requested_path = (base_dir / filename).resolve()
        requested_path.relative_to(base_dir)  # raises ValueError on traversal
    except (ValueError, RuntimeError):
        logger.warning("Path traversal attempt blocked: %s", filename)
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not requested_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return {"filename": filename, "content": requested_path.read_text()}


logger.info("ScriptMesh orchestrator service configured")
