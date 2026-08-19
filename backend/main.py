import os
import re
import json
import yaml
import shlex
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
ANSIBLE_DIR = BASE_DIR.parent / "ansible"
INVENTORY = ANSIBLE_DIR / "inventory" / "hosts.yml"

ANSIBLE_TIMEOUT = 20

app = FastAPI(
    title="Lab Control",
    description="Ansible Lab Master Console",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# MODELS
# ============================================================

class CommandRequest(BaseModel):
    hosts: List[str]
    command: str = Field(min_length=1)


class PackageRequest(BaseModel):
    hosts: List[str]
    package: str = Field(min_length=1)


class PowerRequest(BaseModel):
    hosts: List[str]


# ============================================================
# HELPERS
# ============================================================

def run_process(
    command: List[str],
    timeout: int = ANSIBLE_TIMEOUT,
) -> subprocess.CompletedProcess:
    """
    Execute a subprocess safely without shell=True.
    """

    try:
        return subprocess.run(
            command,
            cwd=str(ANSIBLE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            returncode=124,
            stdout=exc.stdout or "",
            stderr=f"Command timed out after {timeout} seconds",
        )

    except Exception as exc:
        return subprocess.CompletedProcess(
            command,
            returncode=1,
            stdout="",
            stderr=str(exc),
        )


def inventory_exists() -> bool:
    return INVENTORY.exists()


def load_inventory() -> Dict[str, Any]:
    """
    Load the YAML inventory.

    Expected structure:

    all:
      children:
        linux_clients:
          hosts:
            linux-pc1:
              ...
        windows_clients:
          hosts:
            win-pc1:
              ...
    """

    if not inventory_exists():
        raise RuntimeError(
            f"Inventory not found: {INVENTORY}"
        )

    try:
        with open(INVENTORY, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        return data

    except Exception as exc:
        raise RuntimeError(
            f"Could not read inventory: {exc}"
        )


def get_groups() -> Dict[str, Dict[str, Any]]:
    """
    Return hosts grouped by their Ansible group.
    """

    inventory = load_inventory()

    all_data = inventory.get("all", {})
    children = all_data.get("children", {})

    result = {
        "linux_clients": {},
        "windows_clients": {},
    }

    for group_name in result.keys():

        group = children.get(group_name, {})

        hosts = group.get("hosts", {})

        if isinstance(hosts, dict):
            result[group_name] = hosts

    return result


def get_host_info(hostname: str) -> Optional[Dict[str, Any]]:
    """
    Find a host and determine whether it is Linux or Windows.
    """

    groups = get_groups()

    if hostname in groups["linux_clients"]:
        info = groups["linux_clients"][hostname] or {}

        return {
            "name": hostname,
            "group": "linux_clients",
            "os": "linux",
            "info": info,
        }

    if hostname in groups["windows_clients"]:
        info = groups["windows_clients"][hostname] or {}

        return {
            "name": hostname,
            "group": "windows_clients",
            "os": "windows",
            "info": info,
        }

    return None


def get_all_hosts() -> List[Dict[str, Any]]:
    """
    Return all hosts from inventory.
    """

    groups = get_groups()

    hosts = []

    for hostname, info in groups["linux_clients"].items():
        info = info or {}

        hosts.append(
            {
                "name": hostname,
                "hostname": hostname,
                "group": "linux_clients",
                "os": "linux",
                "ip": info.get("ansible_host", ""),
                "mac_address": info.get("mac_address", ""),
            }
        )

    for hostname, info in groups["windows_clients"].items():
        info = info or {}

        hosts.append(
            {
                "name": hostname,
                "hostname": hostname,
                "group": "windows_clients",
                "os": "windows",
                "ip": info.get("ansible_host", ""),
                "mac_address": info.get("mac_address", ""),
            }
        )

    return hosts


def windows_password(host_info: Dict[str, Any]) -> str:
    """
    Get Windows password.

    Priority:

    1. Environment variable LAB_WINDOWS_PASSWORD
    2. ansible_password from inventory
    """

    env_password = os.getenv("LAB_WINDOWS_PASSWORD")

    if env_password:
        return env_password

    info = host_info.get("info", {})

    return str(info.get("ansible_password", "") or "")


def build_ansible_command(
    hostname: str,
    module: str,
    extra_vars: Optional[Dict[str, Any]] = None,
    module_args: Optional[str] = None,
) -> List[str]:

    host_info = get_host_info(hostname)

    if not host_info:
        raise ValueError(
            f"Unknown host: {hostname}"
        )

    command = [
        "ansible",
        hostname,
        "-i",
        str(INVENTORY),
        "-m",
        module,
    ]

    if module_args:
        command.extend(
            [
                "-a",
                module_args,
            ]
        )

    vars_to_send = dict(extra_vars or {})

    # Windows requires password for NTLM.
    if host_info["os"] == "windows":

        password = windows_password(host_info)

        if password:
            vars_to_send["ansible_password"] = password

    if vars_to_send:

        command.extend(
            [
                "-e",
                json.dumps(vars_to_send),
            ]
        )

    return command


# ============================================================
# STATUS CHECK
# ============================================================

def check_host_status(hostname: str) -> Dict[str, Any]:
    """
    IMPORTANT:

    Linux:
        ansible.builtin.ping

    Windows:
        ansible.windows.win_ping

    This is the main fix for the dashboard status problem.
    """

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "name": hostname,
            "online": False,
            "status": "unknown",
            "error": "Host not found in inventory",
        }

    if host_info["os"] == "windows":

        module = "ansible.windows.win_ping"

    else:

        module = "ansible.builtin.ping"

    try:

        command = build_ansible_command(
            hostname=hostname,
            module=module,
        )

        result = run_process(
            command,
            timeout=ANSIBLE_TIMEOUT,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        online = (
            result.returncode == 0
            and (
                "SUCCESS" in stdout
                or '"ping": "pong"' in stdout
                or '"ping":"pong"' in stdout
            )
        )

        if online:

            return {
                "name": hostname,
                "hostname": hostname,
                "online": True,
                "status": "online",
                "os": host_info["os"],
                "group": host_info["group"],
                "ip": host_info["info"].get(
                    "ansible_host",
                    "",
                ),
                "error": "",
            }

        error_text = stderr.strip()

        if not error_text:
            error_text = stdout.strip()

        return {
            "name": hostname,
            "hostname": hostname,
            "online": False,
            "status": "offline",
            "os": host_info["os"],
            "group": host_info["group"],
            "ip": host_info["info"].get(
                "ansible_host",
                "",
            ),
            "error": error_text[-2000:],
        }

    except Exception as exc:

        return {
            "name": hostname,
            "hostname": hostname,
            "online": False,
            "status": "offline",
            "os": host_info["os"],
            "group": host_info["group"],
            "ip": host_info["info"].get(
                "ansible_host",
                "",
            ),
            "error": str(exc),
        }


# ============================================================
# STATUS ENDPOINT
# ============================================================

@app.get("/api/hosts")
def api_hosts():

    try:

        hosts = get_all_hosts()

        results = []

        for host in hosts:

            status = check_host_status(
                host["name"]
            )

            merged = {
                **host,
                **status,
            }

            results.append(merged)

        online_count = sum(
            1
            for host in results
            if host.get("online") is True
        )

        return {
            "hosts": results,
            "total": len(results),
            "online": online_count,
            "offline": len(results) - online_count,
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# Alias in case your frontend uses /hosts
@app.get("/hosts")
def hosts_alias():

    return api_hosts()


# ============================================================
# REFRESH STATUS
# ============================================================

@app.post("/api/hosts/refresh")
def refresh_hosts():

    return api_hosts()


@app.get("/api/status")
def api_status():

    return api_hosts()


# ============================================================
# SINGLE HOST STATUS
# ============================================================

@app.get("/api/hosts/{hostname}/status")
def single_host_status(hostname: str):

    result = check_host_status(hostname)

    return result


# ============================================================
# RUN COMMAND
# ============================================================

def run_command_on_host(
    hostname: str,
    command_text: str,
) -> Dict[str, Any]:

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "host": hostname,
            "success": False,
            "error": "Host not found",
        }

    if host_info["os"] == "windows":

        module = "ansible.windows.win_shell"

    else:

        module = "ansible.builtin.shell"

    try:

        command = build_ansible_command(
            hostname=hostname,
            module=module,
            module_args=command_text,
        )

        result = run_process(
            command,
            timeout=60,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        success = result.returncode == 0

        return {
            "host": hostname,
            "success": success,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    except Exception as exc:

        return {
            "host": hostname,
            "success": False,
            "error": str(exc),
        }


@app.post("/api/command")
def api_command(req: CommandRequest):

    if not req.hosts:
        raise HTTPException(
            status_code=400,
            detail="No hosts selected",
        )

    results = []

    for hostname in req.hosts:

        results.append(
            run_command_on_host(
                hostname,
                req.command,
            )
        )

    return {
        "command": req.command,
        "results": results,
    }


# Alternative endpoint
@app.post("/api/run-command")
def api_run_command(req: CommandRequest):

    return api_command(req)


# ============================================================
# PACKAGE INSTALLATION
# ============================================================

def install_package_on_host(
    hostname: str,
    package: str,
) -> Dict[str, Any]:

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "host": hostname,
            "success": False,
            "error": "Host not found",
        }

    try:

        if host_info["os"] == "linux":

            # Debian/Ubuntu lab machines
            module = "ansible.builtin.apt"

            command = build_ansible_command(
                hostname=hostname,
                module=module,
                module_args=f"name={shlex.quote(package)} state=present",
            )

        else:

            # Windows:
            # Prefer Chocolatey if available.
            module = "chocolatey.chocolatey.win_chocolatey"

            command = build_ansible_command(
                hostname=hostname,
                module=module,
                module_args=(
                    f"name={shlex.quote(package)} "
                    f"state=present"
                ),
            )

        result = run_process(
            command,
            timeout=120,
        )

        stdout = result.stdout or ""
        stderr = result.stderr or ""

        return {
            "host": hostname,
            "package": package,
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    except Exception as exc:

        return {
            "host": hostname,
            "package": package,
            "success": False,
            "error": str(exc),
        }


@app.post("/api/package")
def api_package(req: PackageRequest):

    if not req.hosts:

        raise HTTPException(
            status_code=400,
            detail="No hosts selected",
        )

    results = []

    for hostname in req.hosts:

        results.append(
            install_package_on_host(
                hostname,
                req.package,
            )
        )

    return {
        "package": req.package,
        "results": results,
    }


@app.post("/api/install-package")
def api_install_package(req: PackageRequest):

    return api_package(req)


# ============================================================
# FACTS
# ============================================================

def get_linux_facts(hostname: str) -> Dict[str, Any]:

    command = build_ansible_command(
        hostname,
        "ansible.builtin.setup",
    )

    result = run_process(
        command,
        timeout=60,
    )

    return {
        "success": result.returncode == 0,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


def get_windows_facts(hostname: str) -> Dict[str, Any]:

    powershell_command = """
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'

$result = @{
    hostname = $env:COMPUTERNAME
    os = $os.Caption
    version = $os.Version
    ram_gb = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)
    disk_total_gb = [math]::Round($disk.Size / 1GB, 2)
    disk_free_gb = [math]::Round($disk.FreeSpace / 1GB, 2)
}

$result | ConvertTo-Json -Compress
"""

    command = build_ansible_command(
        hostname,
        "ansible.windows.win_shell",
        module_args=powershell_command,
    )

    result = run_process(
        command,
        timeout=60,
    )

    return {
        "success": result.returncode == 0,
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


@app.get("/api/hosts/{hostname}/facts")
def api_facts(hostname: str):

    host_info = get_host_info(hostname)

    if not host_info:

        raise HTTPException(
            status_code=404,
            detail="Host not found",
        )

    if host_info["os"] == "windows":

        result = get_windows_facts(hostname)

    else:

        result = get_linux_facts(hostname)

    return {
        "host": hostname,
        **result,
    }


# Alias
@app.get("/api/facts/{hostname}")
def api_facts_alias(hostname: str):

    return api_facts(hostname)


# ============================================================
# REBOOT
# ============================================================

def reboot_host(hostname: str) -> Dict[str, Any]:

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "host": hostname,
            "success": False,
            "error": "Host not found",
        }

    try:

        if host_info["os"] == "windows":

            module = "ansible.windows.win_reboot"

            command = build_ansible_command(
                hostname,
                module,
                module_args="reboot_timeout=600",
            )

        else:

            module = "ansible.builtin.reboot"

            command = build_ansible_command(
                hostname,
                module,
                module_args="reboot_timeout=600",
            )

        result = run_process(
            command,
            timeout=180,
        )

        return {
            "host": hostname,
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }

    except Exception as exc:

        return {
            "host": hostname,
            "success": False,
            "error": str(exc),
        }


@app.post("/api/reboot")
def api_reboot(req: PowerRequest):

    if not req.hosts:

        raise HTTPException(
            status_code=400,
            detail="No hosts selected",
        )

    results = []

    for hostname in req.hosts:

        results.append(
            reboot_host(hostname)
        )

    return {
        "action": "reboot",
        "results": results,
    }


# ============================================================
# SHUTDOWN
# ============================================================

def shutdown_host(hostname: str) -> Dict[str, Any]:

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "host": hostname,
            "success": False,
            "error": "Host not found",
        }

    try:

        if host_info["os"] == "windows":

            module = "ansible.windows.win_shell"

            ps_command = (
                "Stop-Computer -Force"
            )

            command = build_ansible_command(
                hostname,
                module,
                module_args=ps_command,
            )

        else:

            module = "ansible.builtin.command"

            command = build_ansible_command(
                hostname,
                module,
                module_args="shutdown -h now",
            )

        result = run_process(
            command,
            timeout=30,
        )

        return {
            "host": hostname,
            "success": result.returncode == 0,
            "returncode": result.returncode,
            "stdout": result.stdout or "",
            "stderr": result.stderr or "",
        }

    except Exception as exc:

        return {
            "host": hostname,
            "success": False,
            "error": str(exc),
        }


@app.post("/api/shutdown")
def api_shutdown(req: PowerRequest):

    if not req.hosts:

        raise HTTPException(
            status_code=400,
            detail="No hosts selected",
        )

    results = []

    for hostname in req.hosts:

        results.append(
            shutdown_host(hostname)
        )

    return {
        "action": "shutdown",
        "results": results,
    }


# ============================================================
# WAKE ON LAN
# ============================================================

def wake_on_lan(mac_address: str) -> bool:

    clean_mac = re.sub(
        r"[^0-9A-Fa-f]",
        "",
        mac_address,
    )

    if len(clean_mac) != 12:
        return False

    mac_bytes = bytes.fromhex(clean_mac)

    packet = b"\xff" * 6 + mac_bytes * 16

    sock = socket.socket(
        socket.AF_INET,
        socket.SOCK_DGRAM,
    )

    try:

        sock.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1,
        )

        sock.sendto(
            packet,
            ("255.255.255.255", 9),
        )

        return True

    except Exception:

        return False

    finally:

        sock.close()


def wake_host(hostname: str) -> Dict[str, Any]:

    host_info = get_host_info(hostname)

    if not host_info:

        return {
            "host": hostname,
            "success": False,
            "error": "Host not found",
        }

    mac = host_info["info"].get(
        "mac_address",
        "",
    )

    if not mac:

        return {
            "host": hostname,
            "success": False,
            "error": "MAC address not configured",
        }

    success = wake_on_lan(mac)

    return {
        "host": hostname,
        "mac_address": mac,
        "success": success,
    }


@app.post("/api/wake")
def api_wake(req: PowerRequest):

    if not req.hosts:

        raise HTTPException(
            status_code=400,
            detail="No hosts selected",
        )

    results = []

    for hostname in req.hosts:

        results.append(
            wake_host(hostname)
        )

    return {
        "action": "wake",
        "results": results,
    }


# Alias
@app.post("/api/wol")
def api_wol(req: PowerRequest):

    return api_wake(req)


# ============================================================
# INVENTORY
# ============================================================

@app.get("/api/inventory")
def api_inventory():

    try:

        return {
            "inventory": load_inventory(),
            "hosts": get_all_hosts(),
        }

    except Exception as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "ok",
        "inventory": str(INVENTORY),
        "inventory_exists": inventory_exists(),
    }


@app.get("/")
def root():

    return {
        "name": "LAB CONTROL",
        "status": "running",
        "message": "Ansible master console",
    }


# ============================================================
# STARTUP INFORMATION
# ============================================================

@app.on_event("startup")
def startup():

    print("=" * 60)
    print("LAB CONTROL")
    print("=" * 60)

    print(f"Backend directory : {BASE_DIR}")
    print(f"Ansible directory : {ANSIBLE_DIR}")
    print(f"Inventory         : {INVENTORY}")

    if inventory_exists():

        print("Inventory         : OK")

        try:

            hosts = get_all_hosts()

            print(
                f"Hosts             : {len(hosts)}"
            )

            for host in hosts:

                print(
                    f"  - {host['name']:<15} "
                    f"{host['os']:<8} "
                    f"{host['ip']}"
                )

        except Exception as exc:

            print(
                f"Inventory error   : {exc}"
            )

    else:

        print("Inventory         : NOT FOUND")

    print("=" * 60)
