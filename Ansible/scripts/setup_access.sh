#!/usr/bin/env bash
# One-time access bootstrap: copies the per-host SSH key to every reachable
# machine using the vault-stored passwords, then runs the bootstrap playbook.
#
# Requirements:
#   - ~/.vault_pass.txt exists (created during setup)
#   - group_vars/all/secrets.yml holds the REAL passwords (ansible-vault edit group_vars/all/secrets.yml)
#
# Usage: scripts/setup_access.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f "$HOME/.vault_pass.txt" ]; then
  echo "ERROR: ~/.vault_pass.txt missing" >&2; exit 1
fi

TMP=$(mktemp /tmp/vault-XXXX.yml)
trap 'rm -f "$TMP"' EXIT
ansible-vault view --vault-password-file "$HOME/.vault_pass.txt" group_vars/all/secrets.yml > "$TMP"

python3 - "$TMP" <<'PY'
import sys, yaml, re, subprocess

with open(sys.argv[1]) as f:
    secrets = yaml.safe_load(f)["vault_hosts"]

hosts = {}   # inventory alias -> ansible_host
section = None
with open("inventory/inventory.ini") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^\[(\w+)\]$", line)
        if m:
            section = m.group(1)
            continue
        if section:
            parts = line.split()
            addr = None
            for p in parts[1:]:
                if p.startswith("ansible_host="):
                    addr = p.split("=", 1)[1]
            hosts[parts[0]] = addr or parts[0]

for name, info in secrets.items():
    pw = info.get("password", "")
    user = info.get("user", "")
    addr = hosts.get(name, name)
    if not pw or pw.startswith("PLACEHOLDER"):
        print(f"SKIP {name}: password not set (ansible-vault edit group_vars/all/secrets.yml)")
        continue
    print(f"PROCESS {name} ({user}@{addr})")
    cmd = ["sshpass", "-p", pw, "ssh-copy-id", "-i", f"vault/keys/{name}.pub",
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
           "-o", "PubkeyAuthentication=no", f"{user}@{addr}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    out = (proc.stderr or "").strip().splitlines()
    if proc.returncode == 0:
        print(f"  OK: key installed for {name}")
    else:
        print(f"  FAIL: {out[-1] if out else proc.returncode}")
PY

echo
echo "Running bootstrap playbook (Linux sudoers / Windows OpenSSH)..."
ansible-playbook -i inventory/inventory.ini playbooks/bootstrap.yml
