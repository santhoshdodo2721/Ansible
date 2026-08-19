from flask import Flask, render_template, request, jsonify
import subprocess
import configparser
import re
import os
import json
from groq import Groq

app = Flask(__name__)

ANSIBLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INVENTORY_PATH = os.path.join(ANSIBLE_DIR, "inventory", "inventory.ini")
SECRETS_FILE = os.path.join(ANSIBLE_DIR, "group_vars", "all", "secrets.yml")
VAULT_PASS_FILE = os.path.expanduser("~/.vault_pass.txt")
GENERATED_PLAYBOOK = os.path.join(ANSIBLE_DIR, "playbooks", "generated_playbook.yml")

ANSIBLE_LINT = os.path.join(ANSIBLE_DIR, "dashboard", ".venv", "bin", "ansible-lint")
if not os.path.exists(ANSIBLE_LINT):
    ANSIBLE_LINT = "ansible-lint"

if os.path.exists(VAULT_PASS_FILE):
    os.environ["ANSIBLE_VAULT_PASSWORD_FILE"] = VAULT_PASS_FILE

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
KNOWN_GOOD_MODULES = "apt, service, systemd, copy, template, user, ufw, file, lineinfile, package, get_url, unarchive, cron, debconf, mysql_user, win_chocolatey, win_service, win_package, git, command, shell"


# ---------- Smart-task pipeline (same logic as generate_playbook.py) ----------

def strip_markdown(text):
    return text.replace("```yaml", "").replace("```", "").replace("```json", "").strip()


def fix_common_mistakes(yaml_text):
    return yaml_text


def lint_playbook(filepath):
    result = subprocess.run([ANSIBLE_LINT, "--profile", "min", filepath],
                             cwd=ANSIBLE_DIR, capture_output=True, text=True)
    return result.returncode == 0, result.stdout + result.stderr


def parse_play_recap(output):
    results = {}
    for line in output.split('\n'):
        m = re.match(r'^(\S+)\s*:\s*ok=(\d+)\s*changed=(\d+)\s*unreachable=(\d+)\s*failed=(\d+)', line)
        if m:
            host, ok, changed, unreachable, failed = m.groups()
            results[host] = {
                "ok": int(ok), "changed": int(changed),
                "unreachable": int(unreachable), "failed": int(failed),
                "success": int(failed) == 0 and int(unreachable) == 0
            }
    return results


def check_playbook(filepath):
    result = subprocess.run(["ansible-playbook", "-i", INVENTORY_PATH, filepath, "--check"],
                             cwd=ANSIBLE_DIR, capture_output=True, text=True)
    full = result.stdout + result.stderr
    return result.returncode == 0, full, parse_play_recap(full)


def apply_playbook_file(filepath, limit_hosts=None):
    cmd = ["ansible-playbook", "-i", INVENTORY_PATH, filepath]
    if limit_hosts:
        cmd += ["--limit", ",".join(limit_hosts)]
    result = subprocess.run(cmd, cwd=ANSIBLE_DIR, capture_output=True, text=True)
    full = result.stdout + result.stderr
    return result.returncode == 0, full, parse_play_recap(full)


def get_group_facts(host_group):
    result = subprocess.run(["ansible", host_group, "-i", INVENTORY_PATH, "-m", "setup"],
                             cwd=ANSIBLE_DIR, capture_output=True, text=True)
    match = re.search(r'SUCCESS =>\s*(\{.*?\n\})', result.stdout, re.DOTALL)
    if not match:
        return {"os_family": "unknown", "distribution": "unknown", "pkg_mgr": "unknown"}
    facts = json.loads(match.group(1)).get("ansible_facts", {})
    return {
        "os_family": facts.get("ansible_os_family", "unknown"),
        "distribution": facts.get("ansible_distribution", "unknown"),
        "pkg_mgr": facts.get("ansible_pkg_mgr", "unknown"),
    }


def ai_generate(user_request, facts, previous_error=None):
    prompt = f"""Generate a valid Ansible playbook (YAML only, no explanation, no markdown code fences) that EXACTLY matches the following request:
"{user_request}"

CRITICAL FORMAT RULE: must start with a dash - a YAML list of plays:
- hosts: linux_lab
  become: true
  tasks:
    - name: your task name
      # ...

Target system facts: OS family={facts['os_family']}, Distribution={facts['distribution']}, Package manager={facts['pkg_mgr']}
Use the module matching the request. Only use these modules if relevant: {KNOWN_GOOD_MODULES}.
IMPORTANT: When cloning a repository or creating files without a specific path, always set the destination to /home/cys18/ (or a subfolder there) so it is visible to the user.
DO NOT generate generic templates, do not install packages unless explicitly requested. Only perform the task requested."""
    if previous_error:
        prompt += f"\n\nPrevious attempt failed with this error:\n{previous_error}\n\nFix the specific problem and regenerate the complete corrected playbook."

    resp = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200
    )
    return strip_markdown(resp.choices[0].message.content)


def run_smart_pipeline(user_request, target_group="linux_lab", auto_apply=False):
    """Full pipeline: facts -> generate -> lint -> dry-run check -> (optional) apply.
    Returns a log list of steps for the UI to display."""
    log = []
    facts = get_group_facts(target_group)
    log.append({"step": "facts", "text": f"Detected: {facts}"})

    error_feedback = None
    for attempt in range(1, 5):
        log.append({"step": "generate", "text": f"Attempt {attempt}: generating playbook..."})
        yaml_content = fix_common_mistakes(ai_generate(user_request, facts, error_feedback))

        with open(GENERATED_PLAYBOOK, "w") as f:
            f.write(yaml_content)

        lint_ok, lint_output = lint_playbook(GENERATED_PLAYBOOK)
        if not lint_ok:
            log.append({"step": "lint_fail", "text": f"Attempt {attempt} failed lint:\n{lint_output}\n\nGenerated content:\n{yaml_content}"})
            error_feedback = lint_output
            continue

        success, output, per_host = check_playbook(GENERATED_PLAYBOOK)
        per_host_text = "\n".join(f"  {h}: {'SUCCESS' if r['success'] else 'FAILED'}" for h, r in per_host.items())
        log.append({"step": "dry_run", "text": f"Dry-run result:\n{per_host_text}"})

        if success:
            log.append({"step": "dry_run_pass", "text": "Dry-run passed on ALL machines. Nothing installed yet."})
            if auto_apply:
                real_ok, real_out, real_per_host = apply_playbook_file(GENERATED_PLAYBOOK)
                real_text = "\n".join(f"  {h}: {'SUCCESS' if r['success'] else 'FAILED'}" for h, r in real_per_host.items())
                log.append({"step": "applied", "text": f"Applied for real:\n{real_text}"})
            return log, yaml_content, True

        elif per_host and any(r["success"] for r in per_host.values()):
            succeeded = [h for h, r in per_host.items() if r["success"]]
            log.append({"step": "partial", "text": f"Partial success: {succeeded} passed dry-run."})
            if auto_apply:
                real_ok, real_out, real_per_host = apply_playbook_file(GENERATED_PLAYBOOK, succeeded)
                real_text = "\n".join(f"  {h}: {'SUCCESS' if r['success'] else 'FAILED'}" for h, r in real_per_host.items())
                log.append({"step": "applied", "text": f"Applied for real (only working machines):\n{real_text}"})
            return log, yaml_content, True

        else:
            log.append({"step": "attempt_fail", "text": f"Attempt {attempt} failed on all machines:\n{output}"})
            error_feedback = output

    log.append({"step": "final_fail", "text": "Failed after max attempts — needs manual review."})
    return log, yaml_content, False


# ---------- Inventory parsing (existing) ----------

def parse_inventory():
    groups = {}
    current_group = None
    if not os.path.exists(INVENTORY_PATH):
        return groups
    with open(INVENTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            section_match = re.match(r'^\[([\w-]+)\]$', line)
            if section_match:
                current_group = section_match.group(1)
                if ':children' not in current_group:
                    groups.setdefault(current_group, [])
                continue
            if current_group and not line.startswith('['):
                hostname = line.split()[0]
                groups.setdefault(current_group, []).append(hostname)
    return groups


def parse_inventory_detailed():
    """Parse inventory.ini and return host details including IP addresses."""
    groups = {}
    current_group = None
    if not os.path.exists(INVENTORY_PATH):
        return groups
    with open(INVENTORY_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            section_match = re.match(r'^\[([\w-]+)\]$', line)
            if section_match:
                current_group = section_match.group(1)
                if ':children' not in current_group:
                    groups.setdefault(current_group, [])
                continue
            if current_group and not line.startswith('['):
                parts = line.split()
                hostname = parts[0]
                ip = hostname  # default: hostname IS the address
                user = ""
                for part in parts[1:]:
                    if part.startswith("ansible_host="):
                        ip = part.split("=", 1)[1]
                    elif part.startswith("ansible_user="):
                        user = part.split("=", 1)[1]
                groups.setdefault(current_group, []).append({
                    "name": hostname,
                    "ip": ip,
                    "user": user,
                    "os": "windows" if "windows" in current_group.lower() else "linux",
                })
    return groups


@app.route("/fleet")
def fleet():
    groups = parse_inventory_detailed()
    return render_template("fleet.html", groups=groups)


@app.route("/api/hosts_detailed")
def api_hosts_detailed():
    return jsonify(parse_inventory_detailed())


@app.route("/api/ping_all")
def api_ping_all():
    """Ping all hosts and return per-host online/offline status."""
    groups = parse_inventory_detailed()
    all_hosts = []
    for group_hosts in groups.values():
        all_hosts.extend(group_hosts)

    if not all_hosts:
        return jsonify({"results": {}})

    host_names = [h["name"] for h in all_hosts]
    targets = ",".join(host_names)

    # Determine if any windows hosts
    windows_hosts = set()
    for h in all_hosts:
        if h["os"] == "windows":
            windows_hosts.add(h["name"])

    results = {}

    # Ping linux hosts
    linux_targets = [h for h in host_names if h not in windows_hosts]
    if linux_targets:
        try:
            cmd = ["ansible", ",".join(linux_targets), "-i", INVENTORY_PATH, "-m", "ping", "--one-line", "-f", "20"]
            proc = subprocess.run(cmd, cwd=ANSIBLE_DIR, capture_output=True, text=True, timeout=30)
            output = proc.stdout + proc.stderr
            for line in output.split('\n'):
                m = re.match(r'^(\S+)\s*\|\s*(SUCCESS|UNREACHABLE|FAILED)', line)
                if m:
                    host, status = m.groups()
                    results[host] = status == "SUCCESS"
        except Exception:
            pass

    # Ping windows hosts
    if windows_hosts:
        try:
            cmd = ["ansible", ",".join(windows_hosts), "-i", INVENTORY_PATH, "-m", "win_ping", "--one-line", "-f", "20"]
            proc = subprocess.run(cmd, cwd=ANSIBLE_DIR, capture_output=True, text=True, timeout=30)
            output = proc.stdout + proc.stderr
            for line in output.split('\n'):
                m = re.match(r'^(\S+)\s*\|\s*(SUCCESS|UNREACHABLE|FAILED)', line)
                if m:
                    host, status = m.groups()
                    results[host] = status == "SUCCESS"
        except Exception:
            pass

    # Mark any hosts we didn't get a result for as offline
    for h in host_names:
        if h not in results:
            results[h] = False

    return jsonify({"results": results})


def run_ansible_command(module, args, targets, extra_flags=None):
    cmd = ["ansible", targets, "-i", INVENTORY_PATH, "-m", module]
    if args:
        cmd += ["-a", args]
    if extra_flags:
        cmd += extra_flags
    result = subprocess.run(cmd, cwd=ANSIBLE_DIR, capture_output=True, text=True, timeout=120)
    return result.stdout + result.stderr, result.returncode


VALID_GROUP_RE = re.compile(r'^[\w-]+$')
VALID_HOST_RE = re.compile(r'^[\w.-]+$')


def add_host_to_inventory(hostname, group, host_vars=None):
    """Append a host under [group] in inventory.ini, creating the section if needed."""
    if not VALID_HOST_RE.match(hostname):
        return False, "Invalid hostname/IP."
    if not VALID_GROUP_RE.match(group):
        return False, "Invalid group name."

    lines = []
    if os.path.exists(INVENTORY_PATH):
        with open(INVENTORY_PATH) as f:
            lines = f.readlines()

    existing = "".join(lines)
    if re.search(rf'^\s*{re.escape(hostname)}\b', existing, re.MULTILINE):
        return False, f"'{hostname}' is already in the inventory."

    var_str = " " + " ".join(host_vars) if host_vars else ""
    host_line = f"{hostname}{var_str}\n"
    group_header = f"[{group}]"

    header_idx = next((i for i, l in enumerate(lines) if l.strip() == group_header), None)
    if header_idx is not None:
        insert_at = header_idx + 1
        while insert_at < len(lines) and lines[insert_at].strip() and not lines[insert_at].strip().startswith('['):
            insert_at += 1
        lines.insert(insert_at, host_line)
    else:
        if lines and lines[-1].strip() != "":
            lines.append("\n")
        lines.append(group_header + "\n")
        lines.append(host_line)

    os.makedirs(ANSIBLE_DIR, exist_ok=True)
    with open(INVENTORY_PATH, "w") as f:
        f.writelines(lines)
    return True, f"Added '{hostname}' to [{group}]."


def remove_hosts_from_inventory(hostnames):
    """Remove multiple hosts from inventory.ini."""
    if not os.path.exists(INVENTORY_PATH):
        return True, "Inventory not found."
    
    with open(INVENTORY_PATH) as f:
        lines = f.readlines()
        
    new_lines = []
    removed = []
    for line in lines:
        if line.strip() and not line.strip().startswith('['):
            hostname = line.split()[0]
            if hostname in hostnames:
                removed.append(hostname)
                continue
        new_lines.append(line)
        
    if removed:
        with open(INVENTORY_PATH, "w") as f:
            f.writelines(new_lines)
        return True, f"Removed {len(removed)} host(s)."
    return False, "No hosts removed."


def run_terminal_command(command, hosts, is_windows):
    """Run a raw ad-hoc command on the given hosts via the shell/win_shell module."""
    targets = ",".join(hosts)
    module = "win_shell" if is_windows else "shell"
    extra_flags = None if is_windows else ["--become"]
    return run_ansible_command(module, command, targets, extra_flags=extra_flags)


@app.route("/")
def index():
    groups = parse_inventory()
    return render_template("index.html", groups=groups)


@app.route("/api/hosts")
def api_hosts():
    return jsonify(parse_inventory())

@app.route("/api/add_host", methods=["POST"])
def api_add_host():
    """Add a new Linux or Windows machine to inventory.ini."""
    data = request.get_json()
    name = (data.get("name") or "").strip()
    ip = (data.get("ip") or "").strip()
    os_type = (data.get("os_type") or "linux").strip().lower()
    ansible_user = (data.get("ansible_user") or "").strip()
    group = (data.get("group") or "").strip() or ("windows_lab" if os_type == "windows" else "linux_lab")

    if not name:
        return jsonify({"success": False, "message": "System name is required."})
    if not ip:
        return jsonify({"success": False, "message": "IP address is required."})

    host_vars = []
    if ip:
        host_vars.append(f"ansible_host={ip}")
    if ansible_user:
        host_vars.append(f"ansible_user={ansible_user}")
    if os_type == "windows":
        host_vars.append("ansible_connection=winrm")
        host_vars.append("ansible_winrm_transport=ntlm")
        host_vars.append("ansible_port=5985")

    ok, message = add_host_to_inventory(name, group, host_vars)
    return jsonify({"success": ok, "message": message, "groups": parse_inventory()})


@app.route("/api/remove_hosts", methods=["POST"])
def api_remove_hosts():
    """Remove multiple hosts from inventory.ini."""
    data = request.get_json()
    hosts = data.get("hosts", [])
    if not hosts:
        return jsonify({"success": False, "message": "No hosts selected to remove."})
        
    ok, message = remove_hosts_from_inventory(hosts)
    return jsonify({"success": ok, "message": message, "groups": parse_inventory()})


@app.route("/api/terminal", methods=["POST"])
def api_terminal():
    """Run a raw ad-hoc shell command against selected hosts, for the terminal panel."""
    data = request.get_json()
    command = (data.get("command") or "").strip()
    hosts = data.get("hosts", [])

    if not command:
        return jsonify({"output": "No command given.", "success": False})
    if not hosts:
        return jsonify({"output": "No machines selected.", "success": False})

    groups = parse_inventory()
    windows_hosts = set(groups.get("windows_lab", []))
    is_windows = any(h in windows_hosts for h in hosts)

    try:
        output, rc = run_terminal_command(command, hosts, is_windows)
        return jsonify({"output": output, "success": rc == 0})
    except subprocess.TimeoutExpired:
        return jsonify({"output": "Command timed out.", "success": False})
    except Exception as e:
        return jsonify({"output": f"Error: {str(e)}", "success": False})


@app.route("/api/smart_action", methods=["POST"])
def api_smart_action():
    """Runs the full AI generate -> lint -> dry-run -> (optional apply) pipeline."""
    data = request.get_json()
    prompt = data.get("prompt", "").strip()
    target_group = data.get("target_group", "linux_lab")
    auto_apply = bool(data.get("auto_apply", False))

    if not prompt:
        return jsonify({"success": False, "log": [{"step": "error", "text": "No task description given."}]})

    try:
        log, yaml_content, success = run_smart_pipeline(prompt, target_group, auto_apply)
        return jsonify({"success": success, "log": log, "yaml": yaml_content})
    except Exception as e:
        return jsonify({"success": False, "log": [{"step": "error", "text": f"Error: {str(e)}"}]})


@app.route("/api/action", methods=["POST"])
def api_action():
    data = request.get_json()
    action = data.get("action")
    hosts = data.get("hosts", [])
    package = data.get("package", "").strip()

    if not hosts:
        return jsonify({"output": "No machines selected.", "success": False})

    targets = ",".join(hosts)
    groups = parse_inventory()
    windows_hosts = set(groups.get("windows_lab", []))
    is_windows = any(h in windows_hosts for h in hosts)

    try:
        if action == "ping":
            module = "win_ping" if is_windows else "ping"
            output, rc = run_ansible_command(module, None, targets)

        elif action == "install":
            if not package:
                return jsonify({"output": "No package name given.", "success": False})
            if is_windows:
                module = "win_chocolatey"
                args = f"name={package} state=present"
                output, rc = run_ansible_command(module, args, targets)
            else:
                module = "apt"
                args = f"name={package} state=present update_cache=yes"
                output, rc = run_ansible_command(module, args, targets, extra_flags=["--become"])

        elif action == "restart":
            module = "win_reboot" if is_windows else "reboot"
            output, rc = run_ansible_command(module, None, targets,
                                              extra_flags=None if is_windows else ["--become"])

        elif action == "shutdown":
            if is_windows:
                module = "win_shell"
                args = "shutdown /s /t 5"
            else:
                module = "shell"
                args = "shutdown -h +0"
            output, rc = run_ansible_command(module, args, targets,
                                              extra_flags=None if is_windows else ["--become"])

        elif action == "facts":
            module = "setup"
            output, rc = run_ansible_command(module, None, targets)

        else:
            return jsonify({"output": f"Unknown action: {action}", "success": False})

        return jsonify({"output": output, "success": rc == 0})

    except subprocess.TimeoutExpired:
        return jsonify({"output": "Command timed out.", "success": False})
    except Exception as e:
        return jsonify({"output": f"Error: {str(e)}", "success": False})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
