"""Connection profiles, diagnostics, and platform-specific lab operations."""
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from cryptography.fernet import Fernet
from flask import jsonify, render_template, request

LOCK = threading.RLock()
NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]*$')


class Lab:
    def __init__(self, root):
        self.root = Path(root)
        self.inventory = self.root / 'inventory/inventory.ini'
        self.state = self.root / '.local'

    def profiles(self):
        path = self.state / 'connections.enc'
        if not path.exists():
            return {}
        cipher = Fernet((self.state / 'connections.key').read_bytes())
        return json.loads(cipher.decrypt(path.read_bytes()))

    def save_profiles(self, profiles):
        self.state.mkdir(mode=0o700, exist_ok=True)
        key = self.state / 'connections.key'
        if not key.exists():
            self.atomic(key, Fernet.generate_key())
        self.atomic(self.state / 'connections.enc', Fernet(key.read_bytes()).encrypt(json.dumps(profiles).encode()))

    @staticmethod
    def atomic(path, content):
        fd, tmp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def hosts(self):
        with LOCK:
            profiles = self.profiles()
            hosts = {}
            group = None
            for raw in self.inventory.read_text().splitlines():
                line = raw.strip()
                if not line or line.startswith('#'):
                    continue
                if line.startswith('['):
                    group = line[1:-1] if ':' not in line else None
                    continue
                if not group:
                    continue
                parts = shlex.split(line, comments=True)
                name = parts[0]
                values = dict(p.split('=', 1) for p in parts[1:] if '=' in p)
                profile = profiles.get(name, {})
                windows = values.get('ansible_connection') == 'winrm' or 'windows' in group.lower()
                host = {'name': name, 'ip': values.get('ansible_host', name),
                        'user': values.get('ansible_user', ''), 'group': group,
                        'os': 'windows' if windows else 'linux',
                        'port': int(values.get('ansible_port', values.get('ansible_winrm_port', 5986 if windows else 22))),
                        'key_file': values.get('ansible_ssh_private_key_file', ''),
                        'scheme': values.get('ansible_winrm_scheme', 'https'),
                        'validate_cert': values.get('ansible_winrm_server_cert_validation', 'validate') != 'ignore'}
                host.update({k: v for k, v in profile.items() if k not in ('password', 'sudo_password')})
                host['has_password'] = bool(profile.get('password'))
                host['has_sudo_password'] = bool(profile.get('sudo_password'))
                hosts[name] = host
            return list(hosts.values())

    def save(self, data, verify=False):
        name = str(data.get('name', '')).strip()
        address = str(data.get('ip', '')).strip()
        user = str(data.get('user', '')).strip()
        os_type = data.get('os', 'linux')
        group = str(data.get('group') or ('windows_lab' if os_type == 'windows' else 'linux_lab')).strip()
        if not NAME.fullmatch(name) or not NAME.fullmatch(group):
            raise ValueError('Use letters, numbers, dots, underscores or hyphens for name and group.')
        if not address or any(c.isspace() for c in address) or address.startswith('-') or any(c in address for c in '\"\'[]'):
            raise ValueError('Enter a valid IP address or DNS name.')
        if not user or not re.fullmatch(r'[\w.@\\-]+', user):
            raise ValueError('Enter the SSH or Windows login username.')
        if os_type not in ('linux', 'windows'):
            raise ValueError('Choose Linux or Windows.')
        port = int(data.get('port') or (5986 if os_type == 'windows' else 22))
        if not 1 <= port <= 65535:
            raise ValueError('Port must be between 1 and 65535.')
        key_file = str(data.get('key_file', '')).strip()
        if key_file:
            path = Path(key_file).expanduser()
            if not path.is_absolute():
                path = self.root / path
            if not path.is_file():
                raise ValueError('SSH key file does not exist on this controller.')
            key_file = str(path)
        scheme = data.get('scheme', 'https')
        if scheme not in ('http', 'https'):
            raise ValueError('Choose HTTP or HTTPS for WinRM.')
        profile = {'ip': address, 'user': user, 'os': os_type, 'group': group, 'port': port,
                   'key_file': key_file, 'scheme': scheme, 'validate_cert': bool(data.get('validate_cert', True))}
        with LOCK:
            profiles = self.profiles()
            old = profiles.get(name, {})
            for field in ('password', 'sudo_password'):
                profile[field] = str(data.get(field) or old.get(field, ''))
            if data.get('clear_password'):
                profile['password'] = ''
            if verify:
                status = self.check(dict(profile, name=name), profile_override=profile)
                if not status['ready']:
                    raise ValueError('Connection was not saved; existing settings were kept. ' + status['message'])
            profiles[name] = profile
            # Keep passwords in encrypted local storage; inventory is non-secret.
            values = {'ansible_host': address, 'ansible_user': user, 'ansible_port': str(port)}
            if os_type == 'windows':
                values.update(ansible_connection='winrm', ansible_winrm_transport='ntlm',
                              ansible_winrm_scheme=scheme,
                              ansible_winrm_server_cert_validation='validate' if profile['validate_cert'] else 'ignore',
                              ansible_become_method='runas', ansible_become_user=user)
            else:
                values.update(ansible_connection='ssh', ansible_python_interpreter='auto_silent')
                if key_file:
                    values['ansible_ssh_private_key_file'] = key_file
            host_line = name + ' ' + ' '.join(k + '=' + shlex.quote(v) for k, v in values.items()) + '\n'
            lines = self.inventory.read_text().splitlines(keepends=True)
            lines = [line for line in lines if not (line.strip() and not line.lstrip().startswith(('[', '#')) and line.split()[0] == name)]
            header = '[' + group + ']'
            idx = next((i + 1 for i, line in enumerate(lines) if line.strip() == header), None)
            if idx is None:
                lines.extend(['\n', header + '\n', host_line])
            else:
                lines.insert(idx, host_line)
            self.save_profiles(profiles)
            self.atomic(self.inventory, ''.join(lines).encode())
        return next(h for h in self.hosts() if h['name'] == name)

    def execute(self, host, module, args=None, become=False, timeout=60, profile_override=None):
        with LOCK:
            profile = profile_override if profile_override is not None else self.profiles().get(host['name'], {})
        extras = {'ansible_become': become}
        if profile.get('password'):
            extras['ansible_password'] = profile['password']
        if profile.get('sudo_password'):
            extras['ansible_become_password'] = profile['sudo_password']
        # The legacy vault defaults require a matching vault entry, even for key auth.
        # An explicit local profile can use keys without depending on that vault.
        if profile:
            extras.setdefault('ansible_password', '')
            extras.setdefault('ansible_become_password', profile.get('password', ''))
            extras.update(ansible_host=host['ip'], ansible_user=host['user'], ansible_port=host['port'])
            if host['os'] == 'windows':
                extras.update(ansible_connection='winrm', ansible_winrm_transport='ntlm',
                              ansible_winrm_scheme=host['scheme'],
                              ansible_winrm_server_cert_validation='validate' if host['validate_cert'] else 'ignore',
                              ansible_become_method='runas', ansible_become_user=host['user'])
            else:
                extras.update(ansible_connection='ssh', ansible_python_interpreter='auto_silent')
                if host.get('key_file'):
                    extras['ansible_ssh_private_key_file'] = host['key_file']
        # A saved profile must not load unrelated encrypted group_vars from the
        # legacy inventory. Keep its connection variables in a private temp file.
        inventory_dir = tempfile.TemporaryDirectory(prefix='lab-inventory-') if profile else None
        inventory = self.inventory
        if inventory_dir:
            inventory = Path(inventory_dir.name) / 'inventory.ini'
            self.atomic(inventory, f"[{host['group']}]\n{host['name']}\n".encode())
        cmd = ['ansible', host['name'], '-i', str(inventory), '-m', module,
               '--timeout', '5']
        if args is not None:
            cmd += ['-a', args]
        fd, path = tempfile.mkstemp(prefix='lab-vars-', suffix='.json')
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(extras, stream)
            cmd += ['-e', '@' + path]
            env = dict(os.environ, ANSIBLE_NOCOLOR='1', ANSIBLE_FORCE_COLOR='0', ANSIBLE_STDOUT_CALLBACK='default')
            result = subprocess.run(cmd, cwd=self.root, env=env, capture_output=True, text=True, timeout=timeout)
            output = result.stdout + result.stderr
            for secret in (profile.get('password'), profile.get('sudo_password')):
                if secret:
                    output = output.replace(secret, '[redacted]')
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, 'Operation timed out. Check the address, port and network connection.'
        except OSError as exc:
            return False, str(exc)
        finally:
            os.unlink(path)
            if inventory_dir:
                inventory_dir.cleanup()

    def network_check(self, host):
        checked = datetime.now(timezone.utc).isoformat()
        try:
            with socket.create_connection((host['ip'], host['port']), timeout=3):
                pass
        except OSError as exc:
            return {'state': 'unreachable', 'reachable': False, 'ready': False,
                    'message': f"Cannot reach {host['ip']}:{host['port']}. Check the address, lab network and remote service.",
                    'detail': str(exc), 'checked_at': checked}
        return {'state': 'reachable', 'reachable': True, 'ready': False,
                'message': f"Online: {host['ip']}:{host['port']} is reachable from this controller.",
                'detail': 'Network reachability checked. Authentication is verified separately in the Console.',
                'checked_at': checked}

    def check(self, host, profile_override=None):
        network = self.network_check(host)
        if not network['reachable']:
            return network
        checked = network['checked_at']
        key = host.get('key_file')
        if key and not Path(key if Path(key).is_absolute() else self.root / key).expanduser().is_file():
            return {'state': 'needs_setup', 'reachable': True, 'ready': False,
                    'message': 'Host is reachable. Its configured SSH key is missing; edit the connection to use a password or a valid key.',
                    'detail': 'Missing SSH key: ' + key, 'checked_at': checked}
        ok, output = self.execute(host, 'ansible.windows.win_ping' if host['os'] == 'windows' else 'ansible.builtin.ping', timeout=20, profile_override=profile_override)
        authentication_failed = not ok and ('Permission denied' in output or 'Authentication failed' in output)
        message = 'Authenticated. Ready for lab operations.' if ok else (
            f"SSH rejected {'these' if profile_override is not None else 'the saved'} login credentials. Enter the password that works in your terminal under Configure; the sudo password is separate."
            if authentication_failed and host['os'] == 'linux' else
            'Host is reachable. Authentication or Ansible setup needs attention.')
        return {'state': 'ready' if ok else 'needs_setup', 'reachable': True, 'ready': ok,
                'message': message,
                'detail': output[-3000:], 'checked_at': checked}

    def selected(self, names):
        inventory = {h['name']: h for h in self.hosts()}
        if not isinstance(names, list) or not names or any(n not in inventory for n in names):
            raise ValueError('Select machines from the inventory.')
        return [inventory[n] for n in dict.fromkeys(names)]


def register(app, root, smart_pipeline):
    lab = Lab(root)
    app.config['LAB'] = lab

    @app.before_request
    def local_requests():
        if request.method == 'POST':
            origin = request.headers.get('Origin')
            if origin and origin.rstrip('/') != request.host_url.rstrip('/'):
                return jsonify(success=False, message='Cross-origin requests are not allowed.'), 403

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(success=False, message=str(exc), output=str(exc)), 400

    @app.get('/')
    def console():
        return render_template('lab.html', fleet_view=False)

    @app.get('/fleet')
    def fleet():
        return render_template('lab.html', fleet_view=True)

    @app.get('/api/machines')
    def machines():
        return jsonify(hosts=lab.hosts())

    @app.post('/api/connections')
    def connections():
        data = request.get_json() or {}
        offline = data.get('save_offline') is True
        if offline and (data.get('password') or data.get('sudo_password') or data.get('clear_password')):
            raise ValueError('Offline saves keep existing passwords. Leave password fields blank and the clear-password checkbox unchecked, or use Save & test to verify new credentials.')
        host = lab.save(data, verify=not offline)
        return jsonify(success=True, host=host, verified=not offline, message='Settings saved without testing. Existing passwords were retained.' if offline else 'Connection verified and saved.')

    @app.post('/api/remove')
    def remove():
        data = request.get_json() or {}
        names = {h['name'] for h in lab.selected(data.get('hosts'))}
        with LOCK:
            profiles = lab.profiles()
            lines = lab.inventory.read_text().splitlines(keepends=True)
            lines = [line for line in lines if not (line.strip() and not line.lstrip().startswith(('[', '#')) and line.split()[0] in names)]
            for name in names:
                profiles.pop(name, None)
            lab.save_profiles(profiles)
            lab.atomic(lab.inventory, ''.join(lines).encode())
        return jsonify(success=True, message='Removed from this workspace. Remote machines were not changed.')

    @app.post('/api/check')
    def check():
        data = request.get_json() or {}
        hosts = lab.selected(data.get('hosts')) if data.get('hosts') else lab.hosts()
        with ThreadPoolExecutor(max_workers=20) as pool:
            probe = lab.network_check if data.get('connectivity_only') is True else lab.check
            statuses = list(pool.map(probe, hosts))
        return jsonify(statuses={h['name']: s for h, s in zip(hosts, statuses)})

    @app.post('/api/operate')
    def operate():
        data = request.get_json() or {}
        hosts = lab.selected(data.get('hosts'))
        action = data.get('action')
        allowed = ('ping', 'facts', 'install', 'command', 'restart', 'shutdown')
        if action not in allowed:
            raise ValueError('Choose a supported operation.')
        value = str(data.get('value', '')).strip()
        if action in ('install', 'command') and not value:
            raise ValueError('Enter a package name or command.')
        if action == 'install' and not re.fullmatch(r'[\w.+:@/-]+', value):
            raise ValueError('Enter one package name without spaces or module arguments.')
        def run(host):
            windows = host['os'] == 'windows'
            modules = {'ping': 'ansible.windows.win_ping' if windows else 'ansible.builtin.ping',
                       'facts': 'ansible.windows.setup' if windows else 'ansible.builtin.setup',
                       'install': 'chocolatey.chocolatey.win_chocolatey' if windows else 'ansible.builtin.package',
                       'command': 'ansible.windows.win_shell' if windows else 'ansible.builtin.shell',
                       'restart': 'ansible.windows.win_reboot' if windows else 'ansible.builtin.reboot',
                       'shutdown': 'ansible.windows.win_shell' if windows else 'ansible.builtin.shell'}
            args = value if action == 'command' else json.dumps({'name': value, 'state': 'present'}) if action == 'install' else 'shutdown /s /t 5' if action == 'shutdown' and windows else 'shutdown -h +1' if action == 'shutdown' else None
            become = not windows and (action in ('install', 'restart', 'shutdown') or bool(data.get('become')))
            ok, output = lab.execute(host, modules[action], args, become, timeout=180 if action == 'restart' else 90)
            return {'host': host['name'], 'success': ok, 'output': output}
        with ThreadPoolExecutor(max_workers=20) as pool:
            results = list(pool.map(run, hosts))
        return jsonify(success=all(r['success'] for r in results), results=results)

    @app.post('/api/smart_action')
    def smart():
        data = request.get_json() or {}
        hosts = lab.selected(data.get('hosts'))
        prompt = str(data.get('prompt', '')).strip()
        if not prompt:
            raise ValueError('Describe the task.')
        try:
            log, content, ok = smart_pipeline(prompt, [h['name'] for h in hosts], bool(data.get('auto_apply')))
            return jsonify(success=ok, log=log, yaml=content)
        except Exception as exc:
            return jsonify(success=False, log=[{'step': 'error', 'text': str(exc)}])
