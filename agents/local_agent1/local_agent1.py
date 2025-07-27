from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pathlib import Path
import logging
import json
import subprocess
import os

app = FastAPI()

MANIFEST_PATH = Path("script_manifest.json")

class RunScript(BaseModel):
    script_name: str

# --- API Key Middleware --- #

API_KEY = os.getenv("SCRIPT_MESH_AGENT_KEY", "localagent1secret")
EXCLUDED_PATHS = {"/docs", "/openapi.json", "/"}

@app.middleware("http")
async def verify_agent_key(request: Request, call_next):
    path = request.url.path.rstrip("/")
    if path not in EXCLUDED_PATHS:
        provided_key = request.headers.get("x-api-key")
        if provided_key != API_KEY:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized: Invalid or missing API key"})
    return await call_next(request)

# --- Helper Functions --- #

def load_manifest():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError("Manifest not found")
    
    with open(MANIFEST_PATH) as f:
        return json.load(f)
    
def get_script_path(name: str) -> Path | None:
    try:
        manifest = load_manifest()
        for entry in manifest.get("scripts", []):
            if entry["name"] == name:
                return Path(entry["path"]).resolve()
    except Exception as e:
        logging.warning(f"Failed to read manifest: {e}")

# --- API Functions --- # 

# GET scripts from script_manifest.json
@app.get("/get-scripts")
def get_scripts():

    try:

        data = load_manifest()
        return data
    
    except FileNotFoundError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Run assigned script from script_manifest.json
@app.post("/run-script")
def run_script(payload: RunScript):

    script_path = get_script_path(payload.script_name)

    if not script_path:
        raise HTTPException(status_code=404, detail="Script not found in manifest")

    if not script_path.exists():
        raise HTTPException(status_code=404, detail="Script file missing on disk")

    try:
        result = subprocess.run(["python", str(script_path)], capture_output=True, text=True)

        return {
            "status": "success",
            "script": payload.script_name,
            "output": {
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "returncode": result.returncode
            }
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

# Start agent if called
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("local_agent1:app", host="0.0.0.0", port=5001, reload=True)