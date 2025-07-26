# 🖧 ScriptMesh

**ScriptMesh** A lightweight agent-controller framework built with FastAPI and Docker, designed to securely trigger and manage scripts across a distributed mesh of remote agents.

---
> 🚧 **Development in Progress**  
> ScriptMesh is an early-stage project - stable for local testing, but production hardening is ongoing. Use with caution on public-facing nodes.

## 🚀 Features

- ⚙️ **Remote script execution** across agent nodes via HTTP
- 🔐 **API key authentication** with middleware protection
- 📂 **Script manifest system** to define allowed scripts per agent
- 🔁 **Controller CLI** to interact with easilt agents endpoints
- 🐳 **Docker Hosted** for isolated, clean decentralised orchestration
- 🔍 **Agent discovery**, script listing, and execution tracking

---

## 🧠 How It Works

1. The **Docker container** runs `main.py`, which exposes secure FastAPI endpoints to list, trigger, and query agents.
2. Each **agent** runs its own micro FastAPI server and is governed by a `script_manifest.json` that whitelists which scripts can be executed.
3. The **CLI controller** (`controller.py`) is a simple script to send execution tasks and query requests to the API.

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
                         │      (main.py)     │
                         │   via FastAPI      │
                         └─────────┬──────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌────────────────┐       ┌────────────────┐       ┌────────────────┐
│  Agent Node 1  │       │  Agent Node 2  │       │  Agent Node 3  │
│ (local_agent1) │       │ (local_agent2) │       │ (local_agent3) │
│  Manifest:     │       │  Manifest:     │       │  Manifest:     │
│  hello.py      │       │  backup.sh     │       │  scan_logs.sh  │
└────────────────┘       └────────────────┘       └────────────────┘

  ⬑ API-triggered scripts executed ONLY if whitelisted in `script_manifest.json`
```
---
## ⚙️ Get Started

ScriptMesh currently involves 3 components:

1. **Core Container (main.py)** - Host this via Docker (EC2, ECS, or on-prem):
   ```bash
   docker-compose up --build -d
   ```

2. **Remote Agents (local_agentX.py)** - Run these anywhere:

   - Update the target IP/URL in `main.py` as well as `controller.py` if you are using manually
   - Open the necessary ports on agent hosts
   - Register agents as `systemd` services (recommended)

3. **Controller CLI (controller.py)** - Run this locally to issue commands.

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

- All API endpoints are protected with API key middleware.
- Only whitelisted scripts in the manifest can be executed.
- You control the runtime and exposure of each agent node.

## 🤝 Contributing

Pull requests and ideas are welcome - ScriptMesh is modular by design. If you add new agent types, controller commands, or security features, please document them clearly.
