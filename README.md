# Lab Control — Ansible-based master console

Controls lab PCs (mixed Linux + Windows) from one master node, with a web
dashboard for status, running commands, installing packages, and power
control. Starts on 2 test PCs; scales to 30 by editing one inventory file.

## 1. Master node setup (the PC that runs this)

```bash
sudo apt update
sudo apt install -y python3 python3-pip sshpass
pip3 install ansible
ansible-galaxy collection install ansible.windows community.windows

cd lab-control/backend
pip3 install -r requirements.txt
```

## 2. Prepare the test PCs

### Linux test PC
1. Create/confirm an admin user the master will SSH in as (e.g. `labadmin`).
2. Copy the master's SSH key to it:
   ```bash
   ssh-copy-id labadmin@<linux-pc-ip>
   ```
3. Confirm it: `ssh labadmin@<linux-pc-ip> whoami`

### Windows test PC
1. Windows needs WinRM enabled for Ansible to connect. Run this **on the
   Windows PC** in an elevated PowerShell:
   ```powershell
   Invoke-Expression ((New-Object System.Net.WebClient).DownloadString(
     'https://raw.githubusercontent.com/ansible/ansible-documentation/devel/examples/scripts/ConfigureRemotingForAnsible.ps1'
   ))
   ```
2. Note the Administrator username/password — you'll put them in the inventory.
3. (Optional, for `install_package.yml`) Install [Chocolatey](https://chocolatey.org/install)
   on the Windows PC so package installs work.

## 3. Fill in the inventory

Edit `ansible/inventory/hosts.yml`:
- Set `ansible_host` to each PC's real IP.
- Set `ansible_user` / `ansible_password` for the Windows host (better:
  move the password into an Ansible Vault-encrypted file instead of plain
  text — ask me and I'll wire that up).
- Set `mac_address` for each host (needed only for Wake-on-LAN).

Test connectivity:
```bash
cd ansible
ansible all -m ping
```
Both hosts should return `pong`.

## 4. Run the dashboard

```bash
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open `http://<master-ip>:8000` in a browser. You'll see both test PCs as
cards — click to select them, then run commands, install a package, or
use the power controls.

## 5. Scaling to the other 28 PCs

Just add more entries under `linux_clients` / `windows_clients` in
`ansible/inventory/hosts.yml` — the backend and UI need no changes since
they read the inventory dynamically. For 30 machines, consider grouping
by row/bench (e.g. `bench1`, `bench2`) in the inventory so you can target
subsets later if needed.

## Notes / things worth hardening before wider rollout

- **Secrets**: the Windows password is currently plain text in the
  inventory — fine for a 2-PC test, but switch to `ansible-vault` before
  scaling.
- **Auth on the dashboard itself**: right now anyone who can reach port
  8000 can run commands and reboot machines. Add a login (even basic
  auth) before exposing this beyond your own testing.
- **Wake-on-LAN** only works if the target PC's NIC has WOL enabled in
  BIOS/UEFI and the OS network adapter settings, and the master and
  client are on the same broadcast domain (or you configure directed
  broadcast on your switch/router).
