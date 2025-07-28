# 🖧 ScriptMesh

![License](https://img.shields.io/github/license/gustav0thethird/ScriptMesh)
![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![Status](https://img.shields.io/badge/status-in%20development-orange)

**ScriptMesh** A lightweight agent-controller framework built with FastAPI and Docker, designed to securely trigger and manage scripts across a distributed mesh of remote agents.

## Why ScriptMesh?
ScriptMesh lets you securely trigger approved scripts on remote nodes - without SSH, shared drives, or full agent bloat. Think secure API-driven orchestration, not flaky bash runners, with minimal overhead and maximum control.

---
> 🚧 **Development in Progress**  
> ScriptMesh is an early-stage project - stable for local testing, but production hardening is ongoing. Use with caution on public-facing nodes.

## What's New in v0.2.0
 - **Robust Logging:** The orchestrator, agents and controller now have logging locally and are returned to be accessed via the http response.
   
 - **Unified Agent Structure:** All agent functionality consolidated into a single standard agent.py.

 - **Agent health checks:** Checks for agent heartbeats for agents registered the agent_registry.json every minute to check availability.

 - **Automatic Script & Manifest Generation:** Agents automatically create required scripts (hello_world.py) and manifests (script_manifest.json) on startup if missing.

 - **Enhanced Security:** Fernet encryption now protects stored agent API keys and are encrypted upon registration and decrypted at execution to the corresponding agent.

 - **Simplified Agent Registration:** Automatic registration with the orchestrator on agent startup. Agent name also pulled from host name i.e SERVER1_ScriptMesh_Agent.

 - **Improved Logging & Error Handling:** Robust logging added to orchestrator, agent, and controller components.

---
## 🚀 Features

- ⚙️ **Remote script execution** across agent nodes via HTTP
- 🔐 **API key authentication** with middleware protection
- 📂 **Script manifest system** to define allowed scripts per agent
- 🔁 **Controller CLI** to easily interact with agent endpoints
- 🐳 **Docker Hosted** for isolated, clean decentralised orchestration
- 🔍 **Agent discovery**, script listing, and execution tracking

---

## 🧠 How It Works

1. The **Docker container** runs `orchestrator.py`, which exposes secure FastAPI endpoints to list, trigger, and query agents.
2. Each **agent** runs its own micro FastAPI server and is governed by a `script_manifest.json` that whitelists which scripts can be executed.
3. The **Controller CLI** (`controller.py`) is a simple script to send execution tasks and query requests to the API.

---
```bash
                          ┌────────────────────┐
                          │   Controller CLI   │
                          │   (controller.py)  │
                          └─────────┬──────────┘
                                    │
                                    ▼
                         ┌────────────────────┐
                         │   Core API Server  │
                         │  (orchestrator.py) │
                         │   via FastAPI      │
                         └─────────┬──────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌────────────────┐       ┌────────────────┐       ┌────────────────┐
│  Agent Node 1  │       │  Agent Node 2  │       │  Agent Node 3  │
│    (agent)     │       │   (agent 2)    │       │    (agent3)    │
│  Manifest:     │       │  Manifest:     │       │  Manifest:     │
│  hello.py      │       │  backup.sh     │       │  scan_logs.sh  │
└────────────────┘       └────────────────┘       └────────────────┘

  ⬑ API-triggered scripts executed ONLY if whitelisted in `script_manifest.json`
```
---
## ⚙️ Getting Started

ScriptMesh currently involves 3 components:

**Clone the repo**
  ```bash
  git clone https://github.com/gustav0thethird/ScriptMesh.git
  cd ScriptMesh
  ```


1. **Orchestrator Service (orchestrator.py)** - Host this via Docker (EC2, ECS, or on-prem):
```bash
   cd orchestrator
   docker-compose up --build -d
```

2. **Remote Agents (agent.py)** - Run these anywhere:
```bash
    cd agent
    pip install -r requirements.txt
    py agent.py
```

 - Configure orchestrator URL via environment variables.
 - Recommended: Run agents as systemd services for persistent deployments

3. **Controller CLI (controller.py)** - Run this locally to issue commands to the orchestrator.

> Agents and controller default to `localhost` for development - simply update IPs and ports to scale to real nodes.

---
## 📜 Script Manifest (per agent)

Each agent folder must define its allowed scripts using a `script_manifest.json` file:

```json
{
  "scripts": [
    {
      "name": "hello",
      "path": "hello_world.py"
    }
  ]
}
```

- `name`: A friendly identifier
- `path`: The relative script path within the agent’s directory

---

## 🛡️ Security

- **API key authentication:** Middleware-protected endpoints.

- **Fernet encryption:** Protects stored agent API keys. (stored in cfg/fernet.key)

> If the fernet.key is lost, you must regenerate the key and re-register agents.

- **Manifest whitelisting:** Only scripts defined in the manifest can run.

### 🛡️ Pro Tip: Secure Your Deployment

For production setups, you can modify ScriptMesh to:

- 🔐 **Load API keys from AWS Parameter Store (SSM)**  
  Avoid hardcoding secrets - use `boto3` to fetch keys at runtime based on agent identity.

- 🌐 **Enforce HTTPS using a reverse proxy**  
  Run behind Traefik, Nginx, or Caddy to serve all API traffic over TLS.

This gives you:
- Centralized, encrypted key management
- Safer remote execution across public or hybrid networks

> 💡 ScriptMesh is minimal by design - feel free to adapt it for enterprise-grade hardening.

---

## 🔐 Authentication Model

ScriptMesh uses **API key headers** for both orchestrator and agent security.

### 1. Main Service (`orchestrator.py`)

- Protected via a single global key.
- Required on **all requests to main** (except `/`, `/docs`, `/openapi.json`).

**Header Example:**
```
x-api-key: CHANGEME
```

Set the key in code or as an environment variable:
```bash
export SCRIPT_MESH_MAIN_KEY=CHANGEME
```

---

### 2. Agents (`local_agent1`, `local_agent2`, etc.)

- Each agent has its **own unique API key**, only used by `orchestrator.py` when dispatching.
- These are **not reused** across services.

**Example from `orchestrator.py`:**
```python
AGENT_KEYS = {
    "LOCAL_AGENT1": "localagent1secret",
    "LOCAL_AGENT2": "localagent2secret"
}
```

When main triggers a script:
```http
POST /run-script
x-api-key: localagent1secret
```

Set each agent's key via:
```bash
export SCRIPT_MESH_AGENT_KEY=localagent1secret
```

Or define it in a `.env` file or Docker secret.

---

### 🔁 Key Rotation

- API keys can be rotated per agent or the main controller without affecting others.
- Use `.env` files or Docker secrets for secure injection.

---

### 📖 `/read`

```http
GET /read?filename=<file>
```

Reads a text file from the main service's `/data/` directory (e.g. script logs or outputs).

**Headers:**  
`x-api-key: <your-main-api-key>`

**Query Params:**  
- `filename`: File to read (e.g. `output.log`)

**Response:**
```json
{
  "content": "File contents..."
}
```

---

## 🕒 Scheduled Task Execution (Optional)

ScriptMesh can be paired with **cron jobs**, `systemd timers`, or a custom Python scheduler to automate script execution across your agent mesh - all while respecting the `script_manifest.json` security model.

You can schedule scripts to run on specific agents by setting up cron jobs **on the host** that send HTTP POST requests to agent endpoints.

**Example crontab entry:**

```bash
# At 2:00 AM daily, run the backup script on BACKUP_NODE_1
0 2 * * * curl -X POST http://your-service/trigger-script \
  -H "x-api-key: $SCRIPT_MESH_MAIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"agent": "BACKUP_NODE_1", "script": "nightly_backup"}'
```

> ⚠️ Ensure the main service host has network access to all agents, and that agent keys are properly configured.

---

> 🔧 **Planned:**  
> Future support for pulling logs from agents via a `log_manifest.json`  
> (mirroring `script_manifest.json`), enabling remote log access and sync.

---

## 🤝 Contributing

Pull requests and ideas are welcome - ScriptMesh is modular by design. If you add new agent types, manual controller changes, or security features, please document them clearly.
