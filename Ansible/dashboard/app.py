from flask import Flask
import subprocess
import re
import os
import json
import sys
from groq import Groq

app = Flask(__name__)

ANSIBLE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Use command-line tools installed beside the running Python interpreter.
os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
INVENTORY_PATH = os.path.join(ANSIBLE_DIR, "inventory", "inventory.ini")
VAULT_PASS_FILE = os.path.expanduser("~/.vault_pass.txt")
GENERATED_PLAYBOOK = os.path.join(ANSIBLE_DIR, "playbooks", "generated_playbook.yml")

ANSIBLE_LINT = os.path.join(os.path.dirname(sys.executable), "ansible-lint")
if not os.path.exists(ANSIBLE_LINT):
    ANSIBLE_LINT = "ansible-lint"

if os.path.exists(VAULT_PASS_FILE) and not os.environ.get("ANSIBLE_VAULT_PASSWORD_FILE"):
    os.environ["ANSIBLE_VAULT_PASSWORD_FILE"] = VAULT_PASS_FILE

KNOWN_GOOD_MODULES = "apt, service, systemd, copy, template, user, ufw, file, lineinfile, package, get_url, unarchive, cron, debconf, mysql_user, win_chocolatey, win_service, win_package, git, command, shell"


# ---------- Smart-task pipeline (same logic as generate_playbook.py) ----------

def strip_markdown(text):
    return text.replace("```yaml", "").replace("```", "").replace("```json", "").strip()


CHECK_MODE_GUARD = r'(?:not\s+ansible_check_mode|ansible_check_mode\s*==\s*[Ff]alse|ansible_check_mode\s*!=\s*true)'


def fix_common_mistakes(yaml_text):
    # shell/command/win_shell/win_command tasks are SKIPPED in check mode, so their
    # registered vars lack 'rc'/'stdout'. Ansible evaluates `when` as plain Python,
    # so 'not ansible_check_mode' MUST be the first term for short-circuit to
    # protect attribute access on those skipped vars.
    def _fix_when(m):
        indent, cond = m.group(1), m.group(2)
        if not re.search(r'\.(rc|stdout|stderr|stdout_lines)\b', cond):
            return m.group(0)
        stripped = cond.strip()
        if re.match(r'^(?:\(?not\s+ansible_check_mode|ansible_check_mode\s*==\s*[Ff]alse)', stripped):
            return m.group(0)
        if re.search(CHECK_MODE_GUARD, cond):
            rest = re.sub(r'\s*(?:and\s*)?(?:not\s+ansible_check_mode|ansible_check_mode\s*==\s*[Ff]alse|ansible_check_mode\s*!=\s*true)', '', cond)
            rest = re.sub(r'^\s*and\s+', '', rest).strip()
        else:
            rest = stripped
        return f"{indent}not ansible_check_mode and {rest}"
    return re.sub(r'^(\s*when:)\s*(.+)$', _fix_when, yaml_text, flags=re.MULTILINE)


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


def run_verify_check(platform, targets):
    """Deterministic read-only check of installed packages after an apply,
    so a false 'SUCCESS' is visible in the log."""
    try:
        target_str = ",".join(targets)
        if platform == "windows":
            cmd = ["ansible", target_str, "-i", INVENTORY_PATH, "-m", "win_shell",
                   "-a", "choco list --local-only --limit-output"]
        else:
            cmd = ["ansible", target_str, "-i", INVENTORY_PATH, "-m", "shell",
                   "-a", "dpkg-query -W -f='${Package} ${Version}'"]
        result = subprocess.run(cmd, cwd=ANSIBLE_DIR, capture_output=True, text=True, timeout=120)
        out = (result.stdout or "") + (result.stderr or "")
        lines = [l for l in out.splitlines() if l.strip()]
        return "\n".join(lines[:40])
    except Exception as e:
        return f"Verify check failed: {e}"


def get_target_facts(targets):
    """Gather OS facts for a comma-separated target list (setup works over SSH and WinRM)."""
    result = subprocess.run(["ansible", targets, "-i", INVENTORY_PATH, "-m", "setup"],
                            cwd=ANSIBLE_DIR, capture_output=True, text=True)
    match = re.search(r'SUCCESS =>\s*(\{.*?\n\})', result.stdout, re.DOTALL)
    if not match:
        return {"os_family": "unknown", "distribution": "unknown", "pkg_mgr": "unknown"}
    try:
        facts = json.loads(match.group(1)).get("ansible_facts", {})
    except Exception:
        return {"os_family": "unknown", "distribution": "unknown", "pkg_mgr": "unknown"}
    return {
        "os_family": facts.get("ansible_os_family", "unknown"),
        "distribution": facts.get("ansible_distribution", "unknown"),
        "pkg_mgr": facts.get("ansible_pkg_mgr", "unknown"),
    }


def ai_generate(user_request, facts, platform, hosts_line, previous_error=None):
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("Set GROQ_API_KEY before using smart tasks.")
    client = Groq(api_key=api_key)
    if platform == "windows":
        prompt = f"""Generate a valid Ansible playbook (YAML only, no explanation, no markdown code fences) that EXACTLY matches the following request:
"{user_request}"

CRITICAL FORMAT RULE: must start with a dash - a YAML list of plays:
- hosts: {hosts_line}
  connection: winrm
  become: true
  become_method: runas
  tasks:
    - name: your task name
      # ...

Target system facts: OS family={facts['os_family']}, Distribution={facts['distribution']}, Package manager={facts['pkg_mgr']}
The targets are WINDOWS hosts. Only use Windows modules (e.g. win_chocolatey, win_package, win_service, win_shell, win_copy, win_command). Allowed modules if relevant: {KNOWN_GOOD_MODULES}.
NEVER use Linux modules (apt, get_url for .deb files, unix shell commands).
Installing software should use win_chocolatey (name=<package>, state=present). EXCEPTION: the Chocolatey 'googlechrome' package has a STALE SHA256 checksum that ALWAYS fails on real install ("hashes do not match"). For Google Chrome, use win_package instead: path: 'https://dl.google.com/dl/chrome/install/googlechromestandaloneenterprise64.msi', arguments: '/qn /norestart', creates_path: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', state: present. NEVER generate download-only or check-only tasks for install requests.
ALWAYS end the playbook with a verification task that FAILS if the requested outcome is not present (e.g. for Chrome, a win_shell that checks the chrome.exe path and fails when missing). The verification task MUST have 'when: not ansible_check_mode' so it is skipped during dry-run (check mode) and only enforced on the real run.
DO NOT generate generic templates, do not install packages unless explicitly requested. Only perform the task requested."""
    else:
        prompt = f"""Generate a valid Ansible playbook (YAML only, no explanation, no markdown code fences) that EXACTLY matches the following request:
"{user_request}"

CRITICAL FORMAT RULE: must start with a dash - a YAML list of plays:
- hosts: {hosts_line}
  become: true
  tasks:
    - name: your task name
      # ...

Target system facts: OS family={facts['os_family']}, Distribution={facts['distribution']}, Package manager={facts['pkg_mgr']}
Use the module matching the request. Only use these modules if relevant: {KNOWN_GOOD_MODULES}.
IMPORTANT: When cloning a repository or creating files without a specific path, always set the destination to /home/cys18/ (or a subfolder there) so it is visible to the user.
IMPORTANT: For installing packages on Debian/Ubuntu, prefer apt_repository + apt (name=..., state=present) instead of downloading .deb files with get_url, because .deb downloads fail in check mode.
NEVER generate download-only or check-only tasks for install requests.
ALWAYS end the playbook with a verification task that FAILS if the requested outcome is not present (e.g. for a package install, check with dpkg-query that the package is installed and fail when missing). The verification task MUST have 'when: not ansible_check_mode' so it is skipped during dry-run (check mode) and only enforced on the real run.
DO NOT generate generic templates, do not install packages unless explicitly requested. Only perform the task requested."""
    if previous_error:
        prompt += f"\n\nPrevious attempt failed with this error:\n{previous_error}\n\nFix the specific problem and regenerate the complete corrected playbook."

    resp = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1200
    )
    return strip_markdown(resp.choices[0].message.content)


def _run_platform_pipeline(user_request, platform, subset, auto_apply=False):
    """Full pipeline for one platform subset: facts -> generate -> lint -> dry-run -> (optional) apply.
    Regenerates the playbook (feeding the error back) on lint, dry-run, OR apply failure."""
    log = []
    target_str = ",".join(subset)
    facts = get_target_facts(target_str)
    log.append({"step": "facts", "text": f"[{platform}] Detected: {facts} (targets: {target_str})"})

    error_feedback = None
    yaml_content = ""
    MAX_ATTEMPTS = 4

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log.append({"step": "generate", "text": f"[{platform}] Attempt {attempt}: generating playbook..."})
        yaml_content = fix_common_mistakes(ai_generate(user_request, facts, platform, target_str, error_feedback))

        with open(GENERATED_PLAYBOOK, "w") as f:
            f.write(yaml_content)

        lint_ok, lint_output = lint_playbook(GENERATED_PLAYBOOK)
        if not lint_ok:
            log.append({"step": "lint_fail", "text": f"[{platform}] Attempt {attempt} failed lint:\n{lint_output}\n\nGenerated content:\n{yaml_content}"})
            error_feedback = lint_output[-2000:]
            continue

        success, output, per_host = check_playbook(GENERATED_PLAYBOOK)
        per_host_text = "\n".join(f"  {h}: {'SUCCESS' if r['success'] else 'FAILED'}" for h, r in per_host.items())
        log.append({"step": "dry_run", "text": f"[{platform}] Dry-run result:\n{per_host_text}"})

        dry_ok_hosts = [h for h, r in per_host.items() if r["success"]] if per_host else []

        if not success and not dry_ok_hosts:
            log.append({"step": "attempt_fail", "text": f"[{platform}] Attempt {attempt} failed on all machines:\n{output}"})
            error_feedback = output[-3000:]
            continue

        if success:
            log.append({"step": "dry_run_pass", "text": f"[{platform}] Dry-run passed on ALL selected machines. Nothing installed yet."})
            apply_hosts = subset
        else:
            log.append({"step": "partial", "text": f"[{platform}] Partial success: {dry_ok_hosts} passed dry-run."})
            apply_hosts = dry_ok_hosts

        if not auto_apply:
            return log, yaml_content, True

        real_ok, real_out, real_per_host = apply_playbook_file(GENERATED_PLAYBOOK, apply_hosts)
        all_good = real_ok and bool(real_per_host) and all(r["success"] for r in real_per_host.values())
        real_text = "\n".join(f"  {h}: {'SUCCESS' if r['success'] else 'FAILED'}" for h, r in real_per_host.items())
        verify_text = run_verify_check(platform, apply_hosts)
        log.append({"step": "verify", "text": f"[{platform}] Installed package state:\n{verify_text}"})

        if all_good:
            label = "Applied for real"
            log.append({"step": "applied",
                        "text": f"[{platform}] {label}:\n{real_text}\n\n{real_out}"})
            return log, yaml_content, True

        label = "Applied for real" if success else "Applied for real (only working machines)"
        log.append({"step": "applied_fail",
                    "text": f"[{platform}] {label}:\n{real_text}\n\n{real_out}"})
        error_feedback = real_out[-3000:]
        # Regenerate, feeding the apply error back to the AI so it can switch strategy
        # (e.g. win_chocolatey checksum mismatch -> win_package with direct vendor MSI URL).

    log.append({"step": "final_fail", "text": f"[{platform}] Failed after max attempts — needs manual review."})
    return log, yaml_content, False


def run_smart_pipeline(user_request, target_hosts, auto_apply=False):
    """Full pipeline that only touches the selected hosts.
    Runs once per OS family when the selection mixes Linux and Windows machines.
    Returns a log list of steps for the UI to display."""
    log = []
    groups = parse_inventory()
    valid_hosts = set()
    for hs in groups.values():
        valid_hosts.update(hs)
    windows_hosts = set(groups.get("windows_lab", []))

    unknown = [h for h in target_hosts if h not in valid_hosts]
    if unknown:
        log.append({"step": "error", "text": f"Unknown hosts (not in inventory): {unknown}"})
        return log, "", False

    win_subset = [h for h in target_hosts if h in windows_hosts]
    lin_subset = [h for h in target_hosts if h not in windows_hosts]

    all_ok = True
    last_yaml = ""
    if lin_subset:
        sub_log, yaml_content, ok = _run_platform_pipeline(user_request, "linux", lin_subset, auto_apply)
        log += sub_log
        all_ok = all_ok and ok
        last_yaml = yaml_content or last_yaml
    if win_subset:
        sub_log, yaml_content, ok = _run_platform_pipeline(user_request, "windows", win_subset, auto_apply)
        log += sub_log
        all_ok = all_ok and ok
        last_yaml = yaml_content or last_yaml
    return log, last_yaml, all_ok


# ---------- Lab management ----------
if __package__:
    from .lab import register
else:
    from lab import register


def parse_inventory():
    groups = {}
    for host in app.config['LAB'].hosts():
        groups.setdefault(host['group'], []).append(host['name'])
    return groups


register(app, ANSIBLE_DIR, run_smart_pipeline)

if __name__ == '__main__':
    app.run(host=os.environ.get('HOST', '127.0.0.1'),
            port=int(os.environ.get('PORT', '5000')), debug=False)
