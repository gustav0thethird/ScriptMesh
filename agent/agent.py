from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pathlib import Path
import logging
import json
import subprocess
import os
import sys
import secrets
from datetime import datetime, timezone
import gzip
import shutil
import socket
import requests
import uvicorn
import time

BASE_DIR = Path(__file__).resolve().parent

SCRIPTS_DIR = BASE_DIR / "scripts"
CFG_DIR = BASE_DIR / "cfg"
LOG_DIR = BASE_DIR / "logs"

# Ensure all directories exist
for d in [SCRIPTS_DIR, CFG_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)

MANIFEST_PATH = CFG_DIR / "script_manifest.json"

def compress_old_logs(log_dir: Path, days_threshold=7):
    now = datetime.now()
    compressed_count = 0

    for log_file in log_dir.glob("ScriptMesh-agent-*.log"):
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

# 🔧 FIX: prevent handler duplication on reload
if logger.hasHandlers():
    logger.handlers.clear()

formatter = logging.Formatter(
    "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

file_handler = logging.FileHandler(LOG_DIR / "ScriptMesh-agent.log")
file_handler.setFormatter(formatter)

daily_log_handler = logging.FileHandler(LOG_DIR / f"ScriptMesh-agent-{datetime.now().strftime('%Y-%m-%d')}.log")
daily_log_handler.setFormatter(formatter)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)

logger.addHandler(file_handler)
logger.addHandler(daily_log_handler)
logger.addHandler(console_handler)

logger.info("ScriptMesh agent started and ready to receive requests")

app = FastAPI()

class RunScript(BaseModel):
    script_name: str

# --- Helper Functions  --- #
def get_uptime():
    try:
        with open('/proc/uptime') as f:
            seconds = float(f.readline().split()[0])
            return f"{seconds // 3600:.0f}h {seconds % 3600 // 60:.0f}m"
    except:
        return "unknown"

# --- API Key Middleware --- #

AGENT_API_KEY = os.getenv("SCRIPT_MESH_AGENT_KEY", "localagent1secret")
EXCLUDED_PATHS = {"/docs", "/openapi.json", "/"}


@app.middleware("http")
async def verify_agent_key(request: Request, call_next):
    path = request.url.path.rstrip("/")
    if path not in EXCLUDED_PATHS:
        provided_key = request.headers.get("x-api-key")
        if not secrets.compare_digest(provided_key or "", AGENT_API_KEY):
            return JSONResponse(
                status_code=401,
                content={"detail": "Unauthorized: Invalid or missing API key"},
            )
    return await call_next(request)


# --- Helper Functions --- #

def load_manifest():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError("Manifest not found")

    with open(MANIFEST_PATH) as f:
        return json.load(f)

def get_script_entry(name: str) -> dict | None:
    try:
        manifest = load_manifest()
        for entry in manifest.get("scripts", []):
            if entry["name"] == name:
                return entry
    except Exception as e:
        logger.warning(f"Failed to read manifest for script lookup: {e}", exc_info=True)

def get_hostname():
    return socket.gethostname()

# --- Register with ScriptMesh Orchestrator --- #

ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
SCRIPT_MESH_MAIN_KEY = os.getenv("SCRIPT_MESH_MAIN_KEY", "CHANGEME")

def register_with_orchestrator(retries=5, delay=3):
    agent_name = f"{get_hostname()}_ScriptMesh_Agent"
    agent_url = os.getenv("AGENT_URL", f"http://{socket.gethostbyname(socket.gethostname())}:5001")

    payload = {
        "agent_name": agent_name,
        "url": agent_url,
        "api_key": AGENT_API_KEY
    }

    for attempt in range(1, retries + 1):
        try:
            response = requests.post(
                f"{ORCHESTRATOR_URL}/register-agent",
                json=payload,
                headers={"x-api-key": SCRIPT_MESH_MAIN_KEY},
                timeout=5
            )
            logger.info(f"Successfully registered with orchestrator: {response.json()}")
            break  # Exit loop on success
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{retries} failed: {e}")
            if attempt < retries:
                time.sleep(delay)
            else:
                logger.error("All registration attempts failed.")
        logger.warning(f"Could not register with orchestrator: {e}")

# --- API Functions --- #
@app.get("/heartbeat")
def heartbeat():
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": f"{get_hostname()}_ScriptMesh_Agent",  # ADD VAR HERE AND MAKE SETUP SCRIPT FOR AGENTS THAT PULLS HOSTNAME i.e {HOSTNAME}_ScriptMesh_Agent
        "uptime": get_uptime()
    }

@app.get("/get-scripts")
def get_scripts():
    logger.info("GET /get-scripts called")
    try:
        data = load_manifest()
        logger.info("Manifest loaded successfully")
        return data
    except FileNotFoundError as e:
        logger.warning(f"Manifest file not found: {e}")
        return JSONResponse(
            status_code=404, content={"error": "Manifest file not found"}
        )
    except Exception as e:
        logger.exception("Unexpected error in /get-scripts")
        return JSONResponse(status_code=500, content={"error": "An internal error has occurred."})


@app.post("/run-script")
def run_script(payload: RunScript):
    logger.info(f"POST /run-script called for: {payload.script_name}")

    script_entry = get_script_entry(payload.script_name)

    if not script_entry:
        logger.warning(f"Script not found in manifest: {payload.script_name}")
        raise HTTPException(status_code=404, detail="Script not found in manifest")

    raw_path = Path(script_entry["path"])
    script_path = raw_path if raw_path.is_absolute() else (SCRIPTS_DIR / raw_path).resolve()    

    if not script_path.exists():
        logger.warning(f"Script file missing on disk: {script_path}")
        raise HTTPException(status_code=404, detail="Script file missing on disk")

    try:
        result = subprocess.run(
            ["python", str(script_path)], capture_output=True, text=True
        )

        logger.info(
            f"Executed script '{payload.script_name}' | Return code: {result.returncode}"
        )

        if result.stderr:
            logger.warning(f"stderr from script '{payload.script_name}': {result.stderr.strip()}")

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

    except Exception as e:
        logger.exception(f"Exception while running script: {payload.script_name}")
        return JSONResponse(status_code=500, content={"error": "An internal error has occurred."})


if __name__ == "__main__":
    register_with_orchestrator()
    uvicorn.run(f"{Path(__file__).stem}:app", host="0.0.0.0", port=5001, reload=True)
