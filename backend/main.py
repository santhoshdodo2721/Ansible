"""
Lab Control backend.

Wraps Ansible / ansible-playbook and serves:
    - Host status
    - Host facts
    - Remote command execution
    - Package installation
    - Reboot
    - Shutdown
    - Wake-on-LAN

Run with:

    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import json
import os
import subprocess

from pathlib import Path
from typing import List, Optional

import yaml

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wol import send_magic_packet


# ============================================================================
# PATHS
# ============================================================================

BASE_DIR = Path(__file__).resolve().parent

ANSIBLE_DIR = BASE_DIR.parent / "ansible"

INVENTORY_FILE = ANSIBLE_DIR / "inventory" / "hosts.yml"

PLAYBOOK_DIR = ANSIBLE_DIR / "playbooks"

STATIC_DIR = BASE_DIR / "static"


# ============================================================================
# FASTAPI APPLICATION
# ============================================================================

app = FastAPI(
    title="Lab Control API",
    version="1.0.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# ANSIBLE HELPER
# ============================================================================

def run_ansible(
    args: List[str],
    extra_env: Optional[dict] = None,
) -> dict:
    """
    Execute an Ansible command.

    The command is executed from the Ansible directory so that:

        ansible.cfg
        inventory/
        playbooks/

    are available.
    """

    env = os.environ.copy()

    # Your ansible.cfg already uses JSON callback.
    env["ANSIBLE_STDOUT_CALLBACK"] = "json"
    env["ANSIBLE_LOAD_CALLBACK_PLUGINS"] = "1"

    if extra_env:
        env.update(extra_env)

    try:

        process = subprocess.run(
            args,
            cwd=str(ANSIBLE_DIR),
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    except subprocess.TimeoutExpired:

        return {
            "ok": False,
            "error": "Ansible command timed out after 180 seconds.",
        }

    except Exception as exc:

        return {
            "ok": False,
            "error": str(exc),
        }

    stdout = process.stdout.strip()
    stderr = process.stderr.strip()

    # Try JSON first.
    try:

        data = json.loads(stdout)

        return {
            "ok": process.returncode == 0,
            "data": data,
            "stderr": stderr,
        }

    except json.JSONDecodeError:

        return {
            "ok": process.returncode == 0,
            "raw_stdout": stdout,
            "stderr": stderr,
        }


# ============================================================================
# INVENTORY
# ============================================================================

def load_inventory() -> dict:
    """
    Load the Ansible YAML inventory.
    """

    try:

        with open(INVENTORY_FILE, "r") as file:
            return yaml.safe_load(file) or {}

    except FileNotFoundError:

        raise HTTPException(
            status_code=500,
            detail=f"Inventory not found: {INVENTORY_FILE}",
        )

    except yaml.YAMLError as exc:

        raise HTTPException(
            status_code=500,
            detail=f"Invalid inventory YAML: {exc}",
        )


def all_hosts_with_group() -> List[dict]:
    """
    Return all hosts from the inventory.

    Example:

        [
            {
                "name": "linux-pc1",
                "group": "linux_clients",
                "address": "10.20.21.250",
                "mac_address": "AA:BB:CC:DD:EE:01"
            }
        ]
    """

    inventory = load_inventory()

    hosts = []

    children = (
        inventory
        .get("all", {})
        .get("children", {})
    )

    for group_name, group_data in children.items():

        if not isinstance(group_data, dict):
            continue

        group_hosts = group_data.get("hosts", {}) or {}

        for host_name, host_vars in group_hosts.items():

            host_vars = host_vars or {}

            hosts.append(
                {
                    "name": host_name,
                    "group": group_name,
                    "address": host_vars.get(
                        "ansible_host"
                    ),
                    "mac_address": host_vars.get(
                        "mac_address"
                    ),
                }
            )

    return hosts


def validate_hosts(host_names: List[str]) -> None:
    """
    Make sure all requested hosts exist in the inventory.
    """

    if not host_names:

        raise HTTPException(
            status_code=400,
            detail="At least one host must be selected.",
        )

    available_hosts = {
        host["name"]
        for host in all_hosts_with_group()
    }

    invalid_hosts = [
        host
        for host in host_names
        if host not in available_hosts
    ]

    if invalid_hosts:

        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown host(s): "
                + ", ".join(invalid_hosts)
            ),
        )


def make_target(host_names: List[str]) -> str:
    """
    Convert selected host names into an Ansible host pattern.

    Example:

        ["linux-pc1"]

    becomes:

        linux-pc1
    """

    validate_hosts(host_names)

    return ",".join(host_names)


# ============================================================================
# REQUEST MODELS
# ============================================================================

class CommandRequest(BaseModel):
    hosts: List[str]
    command: str


class InstallRequest(BaseModel):
    hosts: List[str]
    package: str


class PowerRequest(BaseModel):
    hosts: List[str]
    action: str


# ============================================================================
# HOST STATUS
# ============================================================================

@app.get("/api/hosts")
def get_hosts():
    """
    Return all configured hosts and whether they are reachable.

    Each host is tested individually.

    This avoids relying on parsing the combined:
        ansible all -m ping -o

    output.
    """

    hosts = all_hosts_with_group()

    for host in hosts:

        host_name = host["name"]

        result = run_ansible(
            [
                "ansible",
                host_name,
                "-m",
                "ping",
            ]
        )

        host["online"] = result.get(
            "ok",
            False,
        )

    return {
        "hosts": hosts
    }


# ============================================================================
# FACTS
# ============================================================================

@app.get("/api/facts")
def get_facts():
    """
    Gather hardware/network facts from reachable hosts.
    """

    result = run_ansible(
        [
            "ansible",
            "all",
            "-m",
            "setup",
            "-a",
            "gather_subset=hardware,network",
        ]
    )

    facts = {}

    if "data" not in result:

        return {
            "facts": facts,
            "error": result.get(
                "stderr",
                result.get("raw_stdout", ""),
            ),
        }

    data = result["data"]

    for play in data.get("plays", []):

        for task in play.get("tasks", []):

            for host_name, host_result in (
                task.get("hosts", {})
            ).items():

                if host_result.get("unreachable"):
                    continue

                if host_result.get("failed"):
                    continue

                ansible_facts = host_result.get(
                    "ansible_facts",
                    {},
                )

                if not ansible_facts:
                    continue

                mounts = ansible_facts.get(
                    "ansible_mounts",
                    [],
                )

                disk = (
                    mounts[0]
                    if mounts
                    else {}
                )

                facts[host_name] = {
                    "os_family": ansible_facts.get(
                        "ansible_os_family"
                    ),

                    "distribution": ansible_facts.get(
                        "ansible_distribution"
                    ),

                    "memtotal_mb": ansible_facts.get(
                        "ansible_memtotal_mb"
                    ),

                    "memfree_mb": ansible_facts.get(
                        "ansible_memfree_mb"
                    ),

                    "disk_size_total_gb": (
                        round(
                            disk.get(
                                "size_total",
                                0,
                            ) / 1e9,
                            1,
                        )
                        if disk
                        else None
                    ),

                    "disk_size_available_gb": (
                        round(
                            disk.get(
                                "size_available",
                                0,
                            ) / 1e9,
                            1,
                        )
                        if disk
                        else None
                    ),
                }

    return {
        "facts": facts
    }


# ============================================================================
# RUN COMMAND
# ============================================================================

@app.post("/api/run-command")
def run_command(req: CommandRequest):
    """
    Execute a shell command on selected hosts.

    API request:

        {
            "hosts": ["linux-pc1"],
            "command": "hostname"
        }

    Ansible receives:

        target = linux-pc1
        cmd    = hostname
    """

    if not req.command.strip():

        raise HTTPException(
            status_code=400,
            detail="Command cannot be empty.",
        )

    target = make_target(req.hosts)

    result = run_ansible(
        [
            "ansible-playbook",
            str(
                PLAYBOOK_DIR
                / "run_command.yml"
            ),

            "-e",
            json.dumps(
                {
                    "target": target,
                    "cmd": req.command,
                }
            ),
        ]
    )

    return result


# ============================================================================
# INSTALL PACKAGE
# ============================================================================

@app.post("/api/install")
def install_package(req: InstallRequest):
    """
    Install/update a package on selected hosts.

    API request:

        {
            "hosts": ["linux-pc1"],
            "package": "curl"
        }

    Ansible receives:

        target  = linux-pc1
        package = curl
    """

    if not req.package.strip():

        raise HTTPException(
            status_code=400,
            detail="Package name cannot be empty.",
        )

    target = make_target(req.hosts)

    result = run_ansible(
        [
            "ansible-playbook",
            str(
                PLAYBOOK_DIR
                / "install_package.yml"
            ),

            "-e",
            json.dumps(
                {
                    "target": target,
                    "package": req.package,
                }
            ),
        ]
    )

    return result


# ============================================================================
# POWER CONTROL
# ============================================================================

@app.post("/api/power")
def power_action(req: PowerRequest):
    """
    Perform:

        wake
        reboot
        shutdown
    """

    validate_hosts(req.hosts)

    # ------------------------------------------------------------------------
    # WAKE ON LAN
    # ------------------------------------------------------------------------

    if req.action == "wake":

        inventory_hosts = {
            host["name"]: host
            for host in all_hosts_with_group()
        }

        sent = []
        errors = []

        for host_name in req.hosts:

            host_data = inventory_hosts.get(
                host_name
            )

            if not host_data:

                errors.append(
                    f"{host_name}: host not found"
                )

                continue

            mac_address = host_data.get(
                "mac_address"
            )

            if not mac_address:

                errors.append(
                    f"{host_name}: "
                    "no mac_address in inventory"
                )

                continue

            try:

                send_magic_packet(
                    mac_address
                )

                sent.append(host_name)

            except Exception as exc:

                errors.append(
                    f"{host_name}: {exc}"
                )

        return {
            "ok": not errors,
            "sent": sent,
            "errors": errors,
        }

    # ------------------------------------------------------------------------
    # REBOOT / SHUTDOWN VALIDATION
    # ------------------------------------------------------------------------

    if req.action not in (
        "reboot",
        "shutdown",
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "action must be "
                "reboot, shutdown, or wake"
            ),
        )

    # ------------------------------------------------------------------------
    # IMPORTANT
    #
    # Your reboot.yml and shutdown.yml use:
    #
    #     target_list
    #
    # Example:
    #
    #     when: inventory_hostname in target_list
    #
    # Therefore we deliberately send target_list here.
    # ------------------------------------------------------------------------

    result = run_ansible(
        [
            "ansible-playbook",
            str(
                PLAYBOOK_DIR
                / f"{req.action}.yml"
            ),

            "-e",
            json.dumps(
                {
                    "target_list": req.hosts,
                }
            ),
        ]
    )

    return result


# ============================================================================
# STATIC DASHBOARD
# ============================================================================

if STATIC_DIR.exists():

    app.mount(
        "/static",
        StaticFiles(
            directory=str(STATIC_DIR)
        ),
        name="static",
    )


@app.get("/")
def dashboard():
    """
    Serve the dashboard.
    """

    index_file = STATIC_DIR / "index.html"

    if not index_file.exists():

        raise HTTPException(
            status_code=404,
            detail=(
                "Dashboard index.html "
                "not found."
            ),
        )

    return FileResponse(
        str(index_file)
    )
