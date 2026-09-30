# Lab Control — configuration guide

Lab Control runs on a **controller**: the Linux computer where you start this
project. A **remote system** is a Linux or Windows machine you want to manage.
Run the commands in the section for the correct computer.

## Quick start

From the repository root (the folder containing `Ansible/`):

```bash
./Ansible/scripts/start_dashboard.sh
```

If you are already inside `Ansible/`:

```bash
./scripts/start_dashboard.sh
```

Keep that terminal running. Open http://127.0.0.1:5000/fleet for the animated
PC overview, or http://127.0.0.1:5000/ for the control console. Stop the app
with `Ctrl+C`. Saved connections load automatically.

For first-time dependency installation, see section 2 below.

## 1. Fix the current `cys18` connection

The old inventory points to `vault/keys/cys18`. That file is missing on this
controller. An SSH key from another computer is not automatically available here.

For the simplest setup, use the remote machine's login password:

1. On the remote `cys18` Linux machine, run:

   ```bash
   whoami
   hostname -I
   ```

2. Open http://127.0.0.1:5000 on the controller and click **Configure** beside
   `cys18`.
3. Fill the connection form as follows:

   | Field | What to enter |
   | --- | --- |
   | System name | Keep `cys18` |
   | Platform | Linux · SSH |
   | IP address or DNS name | The remote machine's current address from `hostname -I` |
   | Port | `22`, unless you changed the SSH port |
   | Login username | The result of `whoami` on the remote machine |
   | Inventory group | `linux_lab` |
   | Login password | That remote user's login password |
   | Clear saved login password and use SSH keys | Leave **unchecked** |
   | SSH private key path | **Delete `vault/keys/cys18` and leave this field empty** |
   | Sudo password | The remote user's sudo password, if you will install packages or use administrator operations |

4. Click **Save & test connection**.
5. If it fails, click **Details** and follow the troubleshooting section below.

The login password, sudo password, vault password and API key are different
credentials. Use the remote account's password in the login field; do not enter
your vault password or API key there.

### Check the network first

During diagnosis, the controller used `10.20.27.36`, while the stored Linux
addresses were `192.168.1.66`, `.67` and `.68`. Those addresses were unreachable
from this controller. These observations are not a permanent configuration;
check the current addresses on both computers.

Run on the controller:

```bash
ip -brief address
ip route
```

Run on the remote Linux machine:

```bash
hostname -I
```

Use an address reachable from the controller. Connect both computers to the lab
network, or use a network/VPN with a route between them. Different subnets can
work when routing is configured. Changing only the dashboard's address does not
create a network route. If `hostname -I` lists several addresses, choose the lab
network address rather than a Docker, VM-only or loopback address.

## 2. Install and start the controller

The current requirements use Ansible 14 / ansible-core 2.21. Use Python 3.12,
3.13 or 3.14 on the controller; Linux managed nodes need a compatible Python
version. See the [Ansible support matrix](https://docs.ansible.com/projects/ansible-core/stable-2.21/reference_appendices/release_and_maintenance.html).

Open a terminal in the project directory containing `requirements.txt`,
`ansible.cfg` and `dashboard/`. Do not run these commands from inside `dashboard/`.

```bash
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python dashboard/app.py
```

If a Debian-based controller reports that `venv` is missing, install
`python3-venv` using its package manager, then create the environment again.

You can also run `scripts/start_dashboard.sh`; it starts from the correct project
directory even when invoked elsewhere.

Open http://127.0.0.1:5000. Keep the terminal running. Press `Ctrl+C` to stop.
`/fleet` opens the Fleet card view; `/` opens the operations Console.

To use a different local port:

```bash
PORT=5001 .venv/bin/python dashboard/app.py
```

The console defaults to this computer only. It has no multi-user authentication;
keep that default for local use. A Groq API key is not required for the system
table, connection tests or ordinary operations.

## 3. Prepare a remote Linux machine

Run these commands **on the remote Linux machine**, not the controller.
For Debian, Ubuntu or Kali:

```bash
sudo apt update
sudo apt install openssh-server python3
sudo systemctl enable --now ssh
systemctl is-active ssh
python3 --version
whoami
hostname -I
```

For another distribution, use its package manager and SSH service name.

If UFW is already enabled, allow SSH through it:

```bash
sudo ufw status
sudo ufw allow 22/tcp
```

Use your configured SSH port if it is not `22`. Do not enable a new firewall just
to follow this guide. If an existing firewall or network policy restricts access,
allow connections from the controller.

### Verify SSH from the controller

Replace `REMOTE_USER` and `REMOTE_IP` with the actual values:

```bash
ssh REMOTE_USER@REMOTE_IP
```

Check the remote host fingerprint before accepting its first SSH connection.
For a custom port:

```bash
ssh -p 2222 REMOTE_USER@REMOTE_IP
```

If SSH does not work from the controller, fix that first. The dashboard needs
the same network access and valid remote credentials.

Password login must be permitted by the remote SSH configuration if you choose
password authentication. For hosts that intentionally allow only keys, use the
key setup below instead.

## 4. Optional: use SSH keys instead of a login password

Run these commands **on the controller**. Reuse an existing suitable key if you
already have one; do not overwrite it.

To create a separate lab key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lab_control
```

Copy its public key to the remote user:

```bash
ssh-copy-id -i ~/.ssh/lab_control.pub REMOTE_USER@REMOTE_IP
ssh -i ~/.ssh/lab_control REMOTE_USER@REMOTE_IP
```

If the key has a passphrase, load it into an SSH agent in the terminal used to
start the dashboard:

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/lab_control
.venv/bin/python dashboard/app.py
```

In the connection form:

- Enter `~/.ssh/lab_control` in **SSH private key path**. This is the private
  key on the controller, not the `.pub` file and not a path on the remote host.
- Leave **Login password** blank.
- If you previously saved a login password, check **Clear saved login password
  and use SSH keys**.
- Enter a sudo password separately if privileged operations need it.
- Click **Save & test connection**.

You can also leave the key path empty and use a key already loaded into the
controller's SSH agent.

## 5. Add or update systems in the dashboard

Choose **Add system** for a new machine. Choose **Configure** to change an
existing machine's IP, port, username or authentication settings.

Use a unique system name. The name is an inventory alias and need not match the
remote computer's hostname. Default groups are `linux_lab` and `windows_lab`.

The Fleet page displays animated PC tiles in a classroom-style grid, with
system names, addresses, and Online/Offline indicators. Online means the
configured SSH or WinRM port is reachable from this controller; it does not
mean that authentication has succeeded. A blocked service or disconnected
network can show Offline even when the remote computer is powered on.

Fleet refreshes every 30 seconds while the page is visible. Use the tile-size
slider, search and platform filters to adjust the overview. Double-click a
computer or choose Details for its connection information. Configure remains
available on each tile. Authentication and control are handled in the Console. Select cards and choose
**Manage selected in console** to run operations. Changing the controller’s
network does not modify saved addresses or passwords.

Use **Save settings offline** to update a system’s address while it is
unreachable, if you deliberately want to change that configuration. Leave all
password fields blank and the clear-password checkbox unchecked: offline saves
retain existing credentials and cannot replace them. This does not establish
a route to the remote network; reconnect to the lab network or its VPN to
manage systems at their existing addresses.

Save & test verifies authentication before updating a connection. If the test
fails, the existing settings and password are kept; the form stays open with
the reason. Saving updates non-secret settings in `inventory/inventory.ini`. Passwords are
stored separately in encrypted local connection profiles. You do not need to
edit inventory or vault files for hosts configured through this form.

Blank password fields keep previously saved values. Use the explicit checkbox
to clear a saved login password. Clearing the SSH key path removes that key
setting when you save.

**Remove from inventory** removes the connection from this workspace; it does
not shut down, uninstall software from or otherwise alter the remote computer.

## 6. Prepare a remote Windows machine

Open **Windows PowerShell as Administrator** on the remote machine:

```powershell
Enable-PSRemoting -Force
Get-Service WinRM
winrm enumerate winrm/config/listener
Get-NetIPAddress -AddressFamily IPv4
whoami
```

`Enable-PSRemoting` sets up HTTP remoting. Match the dashboard's protocol and
port to the listener shown by the inspection command. The dashboard uses NTLM
for WinRM. See [Ansible's WinRM configuration guide](https://docs.ansible.com/projects/ansible-core/devel/os_guide/windows_winrm.html).

| Dashboard field | Windows connection setting |
| --- | --- |
| Platform | Windows · WinRM |
| IP address | Reachable Windows machine address |
| Login username | Its Windows login account; use an account with the required rights |
| Login password | Its account password, not a Windows Hello PIN |
| WinRM protocol / port | HTTP / `5985` for an HTTP listener; HTTPS / `5986` for an HTTPS listener |
| SSH private key path | Leave empty |
| Validate server certificate | Applies to HTTPS; use a certificate trusted by the controller |

The form defaults to HTTPS / `5986`. Select HTTP / `5985` if that is the listener
you configured. HTTPS requires a configured server certificate and HTTPS
listener; enabling HTTP remoting alone does not create one.

Ensure the Windows firewall allows the configured WinRM port from the controller.
For certificate errors, configure certificate trust or use the matching DNS
name. Only disable certificate validation deliberately for a trusted lab with
a self-signed certificate.

## 7. Understand connection status

| Status | Meaning | Next step |
| --- | --- | --- |
| Ready | The service port is reachable and Ansible authentication succeeded | Select the machine and run an operation |
| Needs setup | The service port is reachable, but the key, login or Ansible setup failed | Open Details; update credentials or remote prerequisites |
| Unreachable | The controller cannot reach the configured SSH/WinRM port | Check address, route, service and firewall |
| Not checked | No completed diagnostic is available | Click Test or Check connections |
| Checking | A connection test is currently running | Wait for its bounded network/authentication check |

A powered-on computer can show **Unreachable** if SSH/WinRM is stopped or blocked.
Use **Check connections** after changing networks or restarting a host.

## 8. Run your first operations

1. Select a machine showing **Ready** using its table checkbox.
2. Click **System facts** to verify that information can be retrieved.
3. Enter `hostname` under **Remote command**, then click **Run command**.
4. Read the per-host results under **Activity & output**.

Commands run as the login user. Select **Use sudo on Linux** only when the
command needs administrator access. Linux package installs and power operations
use sudo automatically; the remote account must have sudo permission. Test
`sudo -v` on that machine if needed.

For **Install package**, enter one package name, such as `curl`. Linux uses its
system package manager; Windows uses Chocolatey and needs that prerequisite
available for the operation. Install requests perform real changes.

Selections may contain Linux and Windows machines: operations use the correct
module per platform. A raw command must still be valid for every selected
platform. Select just Linux for Linux-specific shell commands, or just Windows
for PowerShell-specific commands.

Restart and shutdown require confirmation. After a power operation, run a new
connection check when the machine is expected to be available again.

## 9. Troubleshooting

| Error or symptom | What to check |
| --- | --- |
| SSH key file does not exist on this controller | For password login, clear the key path and supply the login password. For key login, provide an existing private key on this controller. |
| Connection timed out / no route to host | Verify current remote IP, controller network, route/VPN, firewall and service port. |
| Connection refused | Check that SSH or WinRM is running and listening on the configured port. |
| Permission denied / authentication failed | Verify username, account password or installed SSH public key. Test direct SSH for Linux. |
| Python interpreter error | Install a supported Python on the remote Linux host. Saving its profile enables automatic interpreter discovery. |
| Missing sudo password | Enter the remote sudo password under Configure, or use an account with the required existing sudo rights. |
| Windows certificate error | Match HTTPS listener, certificate hostname and controller trust. |
| Windows HTTP/HTTPS mismatch | Inspect the WinRM listener and select the matching protocol and port in Configure. |
| Vault decryption error | Save a connection profile for that host, or provide the correct legacy vault password as described below. |
| Address already in use when starting | Stop the existing dashboard process or choose another `PORT`. |

## 10. Credentials and legacy vault configuration

New connection profiles are encrypted in `.local/connections.enc`; their local
key is `.local/connections.key`. Files have restrictive permissions and `.local/`
is excluded from Git. Keep both files private and back them up together. A
person with access to both files can decrypt the profiles.

Saved connection profiles run with an isolated temporary inventory, so they
do not need the legacy vault password after restarting the dashboard.

Login passwords are not returned by the machines API or passed as command-line
arguments. Connection profiles are specific to this controller; copying the
repository alone does not copy its saved credentials.

Untouched legacy inventory hosts still reference `vault_hosts` from the
encrypted secrets file. For those hosts, create a private vault-password file
on the controller using an editor, then protect it:

```bash
chmod 600 ~/.vault_pass.txt
```

The dashboard uses that file automatically if it exists. Alternatively:

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=/absolute/path/to/your/vault-password-file
.venv/bin/python dashboard/app.py
```

Do not commit passwords, private keys, API keys or the local encryption key.
For everyday setup, use **Configure** rather than changing the legacy vault.

## 11. Verify the application

From the project directory:

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check dashboard/static/lab.js
```

These checks verify application behavior; a **Ready** connection test verifies
access to your actual remote machine.
