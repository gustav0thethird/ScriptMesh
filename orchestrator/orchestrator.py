from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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
from contextlib import asynccontextmanager
from cryptography.fernet import Fernet

# --- Logging Setup --- #

LOG_DIR = Path("logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)

def compress_old_logs(log_dir: Path, days_threshold=7):
    now = datetime.now()
    compressed_count = 0

    for log_file in log_dir.glob("ScriptMesh-orchestrator-*.log"):
        if log_file.suffix == ".gz" or not log_file.name.endswith(".log"):
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

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
file_handler = logging.FileHandler(LOG_DIR / "ScriptMesh-orchestrator.log")
daily_log_handler = logging.FileHandler(LOG_DIR / f"ScriptMesh-orchestrator-{datetime.now().strftime('%Y-%m-%d')}.log")
console_handler = logging.StreamHandler(sys.stdout)

for h in [file_handler, daily_log_handler, console_handler]:
    h.setFormatter(formatter)
    logger.addHandler(h)

logger.info("ScriptMesh orchestrator started and ready to receive requests")

# --- App + Auth Middleware --- #

SCRIPT_MESH_MAIN_KEY = os.getenv("SCRIPT_MESH_MAIN_KEY", "CHANGEME")
EXCLUDED_PATHS = ["/docs", "/openapi.json", "/"]

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start background task
    task = asyncio.create_task(agent_health_loop())
    yield
    task.cancel()  # Clean up on shutdown
    try:
        await task
    except asyncio.CancelledError:
        pass

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def verify_key(request: Request, call_next):
    if request.url.path not in EXCLUDED_PATHS:
        key = request.headers.get("x-api-key", "")
        if key != SCRIPT_MESH_MAIN_KEY:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)

# --- Encryption Setup --- #

FERNET_KEY_PATH = Path("cfg/fernet.key")

# Generate key if it doesn't exist
if not FERNET_KEY_PATH.exists():
    FERNET_KEY_PATH.write_text(Fernet.generate_key().decode())

FERNET_KEY = FERNET_KEY_PATH.read_text().strip()
fernet = Fernet(FERNET_KEY.encode())

def encrypt_string(value: str) -> str:
    return fernet.encrypt(value.encode()).decode()

def decrypt_string(value: str) -> str:
    return fernet.decrypt(value.encode()).decode()

# --- Agent Registry Functions --- #

REGISTRY_PATH = Path("cfg/agent_registry.json")
REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)

def save_registry():
    data_to_save = {}
    for name, agent in registered_agents.items():
        data_to_save[name] = {
            "url": agent["url"],
            "last_seen": agent.get("last_seen"),
            "api_key": encrypt_string(agent["api_key"]),
        }

    REGISTRY_PATH.write_text(json.dumps(data_to_save, indent=2))

def load_registry():
    if REGISTRY_PATH.exists():
        with open(REGISTRY_PATH) as f:
            encrypted_data = json.load(f)

        decrypted_data = {}
        for name, agent in encrypted_data.items():
            decrypted_data[name] = {
                "url": agent["url"],
                "last_seen": agent.get("last_seen"),
                "api_key": decrypt_string(agent["api_key"]),
            }

        return decrypted_data
    return {}

def get_decrypted_registry():
    decrypted = {}
    for name, info in registered_agents.items():
        decrypted[name] = {
            "url": info["url"],
            "api_key": decrypt_string(info["api_key"]),
            "last_seen": info.get("last_seen"),
        }
    return decrypted


registered_agents = load_registry()

# --- Health Check --- # 

agent_status_cache = {}

async def agent_health_loop():
    while True:
        logger.info("[Healthcheck] Pinging all registered agents...")
        for name, info in registered_agents.items():
            try:
                response = requests.get(
                    f"{info['url']}/heartbeat",
                    headers={"x-api-key": decrypt_string(info["api_key"])},
                    timeout=3
                )
                if response.status_code == 200:
                    agent_status_cache[name] = {
                        "status": "online",
                        "last_checked": datetime.utcnow().isoformat()
                    }
                    logger.info(f"[Healthcheck] Agent {name} is online.")
                else:
                    agent_status_cache[name] = {
                        "status": f"error {response.status_code}",
                        "last_checked": datetime.utcnow().isoformat()
                    }
            except Exception:
                agent_status_cache[name] = {
                    "status": "offline",
                    "last_checked": datetime.utcnow().isoformat()
                }
                logger.warning(f"[Healthcheck] Agent {name} is offline or unreachable.")
        
        await asyncio.sleep(60)  # Wait 60 seconds before next check


# --- Pydantic Models --- #

class RunScript(BaseModel):
    run_script: str
    agent: str

class AgentRegistration(BaseModel):
    agent_name: str
    url: str
    api_key: str

# --- Routes --- #

START_TIME = datetime.now(timezone.utc)

@app.get("/health")
def healthcheck():
    return {
        "status": "orchestrator_alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": int((datetime.now(timezone.utc) - START_TIME).total_seconds()),
        "registered_agents": len(registered_agents)
    }

@app.post("/register-agent")
def register_agent(payload: AgentRegistration):
    logger.info(f"Registering agent: {payload.agent_name}")
    now = datetime.utcnow().isoformat()

    registered_agents[payload.agent_name] = {
        "url": payload.url,
        "api_key": encrypt_string(payload.api_key),
        "last_seen": now
    }

    agent_status_cache[payload.agent_name] = {
        "status": "online",
        "last_checked": now
    }

    save_registry()

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
    logger.info("GET /get-agents called")
    try:
        return {
            name: {
                "url": agent["url"],
                "last_seen": agent["last_seen"]
            }
            for name, agent in registered_agents.items()
        }
    except Exception as e:
        logger.exception("Failed to return agent list")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/get-scripts")
def get_agent_scripts(agent: str = Query(...)):
    logger.info(f"GET /get-scripts | Agent: {agent}")
    agent_info = registered_agents.get(agent)

    if not agent_info:
        raise HTTPException(status_code=400, detail="Invalid agent specified")

    try:
        headers = {"x-api-key": decrypt_string(agent_info["api_key"])}
        response = requests.get(f"{agent_info['url']}/get-scripts", headers=headers)
        logger.info(f"Fetched script list from {agent}")
        return response.json()
    except Exception as e:
        logger.exception(f"Error contacting agent {agent}")
        raise HTTPException(status_code=500, detail=f"Agent error: {str(e)}")

@app.post("/trigger-script")
def trigger_agent_script(script: RunScript):
    logger.info(f"POST /trigger-script | Agent: {script.agent} | Script: {script.run_script}")
    agent_info = registered_agents.get(script.agent)

    if not agent_info:
        raise HTTPException(status_code=400, detail="Invalid agent specified")

    try:
        headers = {"x-api-key": decrypt_string(agent_info["api_key"])}
        response = requests.post(
            f"{agent_info['url']}/run-script",
            json={"script_name": script.run_script},
            headers=headers,
        )

        if response.status_code != 200:
            try:
                agent_error = response.json().get("detail", response.text)
            except Exception:
                agent_error = response.text
            raise HTTPException(status_code=response.status_code, detail=agent_error)

        logger.info(f"Triggered script '{script.run_script}' on agent '{script.agent}'")
        return {
            "status": "success",
            "agent": script.agent,
            "output": response.json()
        }

    except Exception as e:
        logger.exception(f"Error triggering script on agent {script.agent}")
        raise HTTPException(status_code=500, detail=f"Agent unreachable: {str(e)}")

@app.get("/read")
def read_file(filename: str = Query(...)):
    logger.info(f"GET /read | Requested file: {filename}")
    base_dir = Path("/data").resolve()
    try:
        requested_path = (base_dir / filename).resolve()
        requested_path.relative_to(base_dir)
    except (ValueError, RuntimeError):
        logger.warning(f"Invalid path attempt: {filename}")
        raise HTTPException(status_code=400, detail="Invalid file path")

    if not requested_path.is_file():
        logger.warning(f"File not found: {requested_path}")
        raise HTTPException(status_code=404, detail="File not found")

    return {"content": requested_path.read_text()}

logger.info("ScriptMesh orchestrator service initialized and running.")
