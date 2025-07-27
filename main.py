from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from pathlib import Path
import requests


# --- API Key Middleware --- #

SCRIPT_MESH_MAIN_KEY = "CHANGEME"

EXCLUDED_PATHS = ["/docs", "/openapi.json", "/"]

app = FastAPI()


@app.middleware("http")
async def verify_key(request: Request, call_next):
    if (
        request.url.path not in EXCLUDED_PATHS
        and request.headers.get("x-api-key") != SCRIPT_MESH_MAIN_KEY
    ):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


# --- Pydantic Model --- #


class RunScript(BaseModel):
    run_script: str
    agent: str


# --- Agent Config --- #

AGENT_KEYS = {"LOCAL_AGENT1": "localagent1secret", "LOCAL_AGENT2": "localagent2secret"}

AGENT_URLS = {
    "LOCAL_AGENT1": "http://host.docker.internal:5001",  # Local host designation
    "LOCAL_AGENT2": "http://host.docker.internal:5002",
}

# --- API Routes --- #


# Read from file within data
# NOTE: Maybe set this up to pull files from agent hosts with list and export based on manifest?
@app.get("/read")
def read_file(filename: str = Query(...)):
    path = Path(f"/data/{filename}")

    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return {"content": path.read_text()}


# Get list of agents assigned under AGENT_URLS
# NOTE: Mabe add registering agents??
@app.get("/get-agents")
def get_agents():

    try:
        return AGENT_URLS

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error contacting agent: {str(e)}")


# Get list of scripts on the agent per manifest
@app.get("/get-scripts")
def get_agent_scripts(agent: str = Query(...)):
    agent_url = AGENT_URLS.get(agent)

    if not agent_url:
        raise HTTPException(status_code=400, detail="Invalid agent specified")

    if agent_url:

        try:
            headers = {"x-api-key": AGENT_KEYS.get(agent)}
            data = requests.get(f"{agent_url}/get-scripts", headers=headers)
            return data.json()

        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Error contacting agent: {str(e)}"
            )


# Trigger script
@app.post("/trigger-script")
def trigger_agent_script(script: RunScript):
    agent_url = AGENT_URLS.get(script.agent)

    try:
        headers = {"x-api-key": AGENT_KEYS.get(script.agent)}
        response = requests.post(
            f"{agent_url}/run-script",
            json={"script_name": script.run_script},
            headers=headers,
        )

        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Agent script execution failed")

        return {"status": "success", "agent": script.agent, "output": response.json()}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent unreachable: {str(e)}")
