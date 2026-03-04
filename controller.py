"""
ScriptMesh Controller CLI
=========================
Interactive command-line client for the ScriptMesh orchestrator.

Configuration via environment variables:
    SCRIPT_MESH_MAIN_KEY   — API key for the orchestrator (required)
    ORCHESTRATOR_HOST      — host:port of the orchestrator (default: localhost:8000)
    ORCHESTRATOR_SCHEME    — http or https (default: http)
"""

import os
import sys
import logging
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_MESH_MAIN_KEY = os.getenv("SCRIPT_MESH_MAIN_KEY", "")
if not SCRIPT_MESH_MAIN_KEY:
    print(
        "ERROR: SCRIPT_MESH_MAIN_KEY environment variable is not set.\n"
        "Export it before running: export SCRIPT_MESH_MAIN_KEY=<your-key>",
        file=sys.stderr,
    )
    sys.exit(1)

ORCHESTRATOR_HOST = os.getenv("ORCHESTRATOR_HOST", "localhost:8000")
ORCHESTRATOR_SCHEME = os.getenv("ORCHESTRATOR_SCHEME", "http")
BASE_URL = f"{ORCHESTRATOR_SCHEME}://{ORCHESTRATOR_HOST}"

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "10"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_file_handler = logging.FileHandler("ScriptMesh-ui.log")
_file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)
logger.addHandler(_file_handler)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADERS = {
    "Content-Type": "application/json",
    "x-api-key": SCRIPT_MESH_MAIN_KEY,
}


def _get(path: str, **kwargs) -> requests.Response:
    return requests.get(f"{BASE_URL}{path}", headers=_HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)


def _post(path: str, **kwargs) -> requests.Response:
    return requests.post(f"{BASE_URL}{path}", headers=_HEADERS, timeout=REQUEST_TIMEOUT, **kwargs)


def get_mode() -> int:
    print("\nAvailable Modes:")
    print("1 → Read file from container")
    print("2 → List agents")
    print("3 → List scripts on agent")
    print("4 → Trigger script on agent")
    print("5 → Agent status / health")
    try:
        return int(input("Enter mode (1-5): "))
    except (ValueError, EOFError):
        print("Invalid input — please enter a number.")
        return 0


def select_agent(prompt: str = "Specify agent name") -> str:
    return input(f"\n{prompt}: ").strip()


def select_script() -> str:
    return input("\nSpecify script name to run: ").strip()


def print_script_response(agent: str, data: dict) -> None:
    try:
        agent_output = data["output"]
        script = agent_output.get("script", "?")
        result = agent_output.get("output", agent_output)

        print(f"\n[{agent}] {script} executed")
        print(f"→ stdout: {result.get('stdout') or '(no output)'}")
        if result.get("stderr"):
            print(f"→ stderr: {result['stderr']}")
        print(f"→ return code: {result.get('returncode', '?')}")
    except Exception as exc:
        print(f"\nError parsing script response: {exc}")
        print(data)


# ---------------------------------------------------------------------------
# API functions
# ---------------------------------------------------------------------------


def api_read(filename: str) -> None:
    try:
        response = _get("/read", params={"filename": filename})
        if response.status_code == 200:
            content = response.json().get("content", "")
            print(f"\n[{filename}]\nContent:\n{content}\n")
            logger.info("Read file '%s' — HTTP %d", filename, response.status_code)
        else:
            detail = response.json().get("detail", response.text)
            print(f"\nFailed to read file: {detail} (HTTP {response.status_code})")
            logger.warning("Read failed for '%s' — %d: %s", filename, response.status_code, detail)
    except requests.exceptions.ConnectionError:
        print(f"\nCould not connect to orchestrator at {BASE_URL}")
        logger.error("Connection error reaching orchestrator")
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        logger.exception("Error in api_read")


def get_agents() -> list[str]:
    """Fetch and display registered agents. Returns a list of agent names."""
    try:
        response = _get("/get-agents")
        if response.status_code == 200:
            data = response.json()
            if not data:
                print("\nNo agents currently registered.")
                return []
            print("\nRegistered agents:")
            for name, info in data.items():
                print(f"  - {name}: {info.get('url', '?')}  (last seen: {info.get('last_seen', '?')})")
            logger.info("Retrieved %d agent(s)", len(data))
            return list(data.keys())
        else:
            detail = response.json().get("detail", response.text)
            print(f"\nFailed to list agents: {detail} (HTTP {response.status_code})")
            logger.warning("get-agents failed — %d: %s", response.status_code, detail)
            return []
    except requests.exceptions.ConnectionError:
        print(f"\nCould not connect to orchestrator at {BASE_URL}")
        logger.error("Connection error reaching orchestrator")
        return []
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        logger.exception("Error in get_agents")
        return []


def get_scripts(agent: str) -> None:
    try:
        response = _get("/get-scripts", params={"agent": agent})
        if response.status_code == 200:
            scripts = response.json().get("scripts", [])
            if not scripts:
                print(f"\nNo scripts found on agent '{agent}'.")
                return
            print(f"\nScripts on '{agent}':")
            for item in scripts:
                print(f"  - {item['name']}  ({item.get('path', '?')})")
            logger.info("Fetched %d script(s) for agent '%s'", len(scripts), agent)
        else:
            detail = response.json().get("detail", response.text)
            print(f"\nScript fetch failed: {detail} (HTTP {response.status_code})")
            logger.warning("get-scripts failed for '%s' — %d: %s", agent, response.status_code, detail)
    except requests.exceptions.ConnectionError:
        print(f"\nCould not connect to orchestrator at {BASE_URL}")
        logger.error("Connection error reaching orchestrator")
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        logger.exception("Error in get_scripts")


def trigger_script(script: str, agent: str) -> None:
    try:
        response = _post(
            "/trigger-script",
            json={"run_script": script, "agent": agent},
        )
        if response.ok:
            print_script_response(agent, response.json())
            logger.info("Triggered script '%s' on agent '%s'", script, agent)
        else:
            try:
                detail = response.json().get("detail", "No details provided")
            except Exception:
                detail = response.text
            print(f"\nScript trigger failed: {detail} (HTTP {response.status_code})")
            logger.warning(
                "trigger-script failed — %d: %s", response.status_code, detail
            )
    except requests.exceptions.ConnectionError:
        print(f"\nCould not connect to orchestrator at {BASE_URL}")
        logger.error("Connection error reaching orchestrator")
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        logger.exception("Error in trigger_script")


def agent_status() -> None:
    try:
        response = _get("/agent-status")
        if response.status_code == 200:
            data = response.json()
            if not data:
                print("\nNo agents registered.")
                return
            print("\nAgent health status:")
            for name, info in data.items():
                status = info.get("status", "unknown")
                checked = info.get("last_checked", "never")
                print(f"  - {name}: {status}  (checked: {checked})")
        else:
            print(f"\nFailed to get agent status (HTTP {response.status_code})")
    except requests.exceptions.ConnectionError:
        print(f"\nCould not connect to orchestrator at {BASE_URL}")
    except Exception as exc:
        print(f"\nUnexpected error: {exc}")
        logger.exception("Error in agent_status")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"\nScriptMesh Controller  (orchestrator: {BASE_URL})")

    while True:
        mode = get_mode()

        try:
            if mode == 1:
                filename = input("\nFile to read: ").strip()
                api_read(filename)

            elif mode == 2:
                get_agents()

            elif mode == 3:
                get_agents()
                agent = select_agent()
                if agent:
                    get_scripts(agent)

            elif mode == 4:
                agents = get_agents()
                agent = select_agent()
                if not agent:
                    print("No agent specified.")
                    continue
                get_scripts(agent)
                script = select_script()
                if script:
                    trigger_script(script, agent)

            elif mode == 5:
                agent_status()

            else:
                print("Invalid mode — choose 1–5.")

        except KeyboardInterrupt:
            print("\nInterrupted.")
            break
        except Exception as exc:
            logger.error("Unexpected error: %s", exc)
            print(f"\nError: {exc}")

        try:
            again = input("\nRun another command? (y/n): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            break
        if again != "y":
            break

    print("\nGoodbye.")


if __name__ == "__main__":
    main()
