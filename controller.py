import requests
import logging

# --- Key Vars --- #

SCRIPT_MESH_MAIN_KEY = "CHANGEME"

host = "localhost:8000"


# --- Global Logging Setup --- #

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

file_handler = logging.FileHandler("ScriptMesh-ui.log")
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)

logger.addHandler(file_handler)


# --- Helper Functions --- #


# Mode picker
def get_mode():
    print("\nAvailable Modes:")
    print("1 → Read file from container")
    print("2 → List agents")
    print("3 → List scripts on agent")
    print("4 → Trigger script on agent")
    try:
        mode = int(input("Enter mode (1-4): "))

    except Exception as e:
        print("Invalid input. Please enter a number!")
        return 0

    return mode


# Choose file to read
def read_content():
    print("Specify file to read:")
    file = input()
    return file


# Select script to run
def select_script():
    print("\nSpecify script to run:")
    script = input()
    return script


# Select agent
def select_agent():
    print("\nSpecify agent to view scripts:")
    agent = input()
    return agent


# Function for verbose output data
def print_script_response(agent, data):
    try:
        agent_output = data["output"]
        script = agent_output["script"]
        result = agent_output["output"]

        print(f"\n[{agent}] {script} executed")
        print(f"→ stdout: {result.get('stdout') or '(no output)'}")
        if result.get("stderr"):
            print(f"→ stderr: {result['stderr']}")
        print(f"→ return code: {result['returncode']}")

    except Exception as e:
        print(f"\nError parsing script response: {e}")
        print(data)


# --- API Functions --- #


# Read remote file
def api_read(file):

    url = f"http://{host}/read?filename={file}"

    headers = {"Content-Type": "application/json", "x-api-key": SCRIPT_MESH_MAIN_KEY}

    response = requests.get(url, headers=headers)
    rc = response.status_code
    data = response.json()

    if rc == 200:
        content = data["content"]
        print(f"Status: {rc}")
        print(f"\n[{file}]")
        print(f"Content:\n{content}\n")
        logger.info(f"Successfully read - {file} - {rc}")

    else:
        print("\n")
        logger.warning(f"Unable to read file - {rc} - {data.get('detail')}")


# Gets agents
def get_agents():
    url = f"http://{host}/get-agents"

    headers = {"Content-Type": "application/json", "x-api-key": SCRIPT_MESH_MAIN_KEY}

    response = requests.get(url, headers=headers)
    rc = response.status_code
    data = response.json()

    if rc == 200:
        print("\nAvailable Agents:")
        for name, url in data.items():
            print(f"- {name}: {url}")
        logger.info(f"Successfully retrieved agents - {rc}")

    else:
        print("\n")
        logger.warning(f"Unable to get agents - {rc} - {data.get("detail")}")


# Gets scripts on required agent
def get_scripts(agent):

    url = f"http://{host}/get-scripts"

    headers = {"Content-Type": "application/json", "x-api-key": SCRIPT_MESH_MAIN_KEY}

    payload = {"agent": agent}

    response = requests.get(url, headers=headers, params=payload)
    rc = response.status_code
    data = response.json()

    if rc == 200:
        scripts = response.json().get("scripts", [])
        print(f"\n Scripts available on {agent}")
        for item in scripts:
            print(f"- {item['name']} ({item['path']})")
        logger.info(f"Successfully fetched scripts for {agent} - {rc}")

    else:
        logger.warning(f"Unable to fetch scripts - {rc} - {data.get('detail')}")


# Trigger remote script
def trigger_script(script, agent):

    url = f"http://{host}/trigger-script"

    headers = {"Content-Type": "application/json", "x-api-key": SCRIPT_MESH_MAIN_KEY}

    payload = {"run_script": script, "agent": agent}

    response = requests.post(url, json=payload, headers=headers)
    rc = response.status_code
    data = response.json()

    if rc == 200:
        print_script_response(agent, data)

    else:
        print("\n")
        logger.warning(f"Unable to trigger script - {rc} - {data.get('detail')}")


# --- Main Loop --- #


def main():

    while True:
        mode = get_mode()

        try:

            if mode == 1:
                file = read_content()
                api_read(file)

            elif mode == 2:
                get_agents()

            elif mode == 3:
                get_agents()
                agent = select_agent()
                get_scripts(agent)

            elif mode == 4:
                get_agents()
                agent = select_agent()
                get_scripts(agent)
                script = select_script()
                trigger_script(script, agent)

            else:
                print("Invalid Mode Selected")

        except Exception as e:
            logger.error(f"Error - {e}")

        again = input("\nRun another command? (y/n)")
        if again != "y":
            break


if __name__ == "__main__":
    main()
