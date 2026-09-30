import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask
from dashboard.lab import Lab, register


class LabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'inventory').mkdir()
        (self.root / 'inventory/inventory.ini').write_text('[linux_lab]\nold ansible_host=192.0.2.1 ansible_user=lab\n\n[windows_lab]\nwin ansible_host=192.0.2.2 ansible_connection=winrm\n')
        self.app = Flask(__name__)
        register(self.app, self.root, lambda *a: ([], '', True))
        self.lab = self.app.config['LAB']
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_encrypted_connection(self):
        with patch.object(self.lab,'check',return_value={'ready':True}):
            response = self.client.post('/api/connections', json={'name':'old','ip':'10.20.27.50','user':'lab','password':'secret-value','os':'linux'})
        self.assertEqual(response.status_code, 200)
        host = response.json['host']
        self.assertEqual(host['ip'], '10.20.27.50')
        self.assertTrue(host['has_password'])
        self.assertNotIn('password', host)
        self.assertNotIn('secret-value', json.dumps(self.client.get('/api/machines').json))
        self.assertNotIn(b'secret-value', (self.root / '.local/connections.enc').read_bytes())
        self.assertNotIn('secret-value', self.lab.inventory.read_text())
        self.assertEqual(len(self.lab.hosts()), 2)
        self.lab.save({'name':'old','ip':'10.20.27.51','user':'lab','os':'linux'})
        self.assertEqual(self.lab.profiles()['old']['password'], 'secret-value')
        self.assertEqual((self.root / '.local/connections.key').stat().st_mode & 0o777, 0o600)

    def test_remove_local_profile(self):
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','password':'secret'})
        response = self.client.post('/api/remove',json={'hosts':['old']})
        self.assertTrue(response.json['success'])
        self.assertEqual([h['name'] for h in self.lab.hosts()],['win'])
        self.assertNotIn('old',self.lab.profiles())

    def test_rejected_credentials_do_not_replace_saved_profile(self):
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','password':'working'})
        previous_inventory=self.lab.inventory.read_bytes()
        previous_profiles=(self.lab.state/'connections.enc').read_bytes()
        with patch.object(self.lab,'check',return_value={'ready':False,'message':'SSH rejected credentials.'}) as check:
            response=self.client.post('/api/connections',json={'name':'old','ip':'10.0.0.2','user':'lab','password':'incorrect'})
            self.assertEqual(check.call_args.kwargs['profile_override']['password'],'incorrect')
        self.assertEqual(response.status_code,400)
        self.assertIn('existing settings were kept',response.json['message'])
        self.assertEqual(self.lab.inventory.read_bytes(),previous_inventory)
        self.assertEqual((self.lab.state/'connections.enc').read_bytes(),previous_profiles)
        self.assertEqual(self.lab.profiles()['old']['password'],'working')

    def test_offline_network_update_keeps_password_and_skips_test(self):
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','password':'working','sudo_password':'sudo-secret'})
        with patch.object(self.lab,'check') as check:
            response=self.client.post('/api/connections',json={'name':'old','ip':'10.1.0.50','user':'lab','save_offline':True})
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.json['verified'])
        check.assert_not_called()
        self.assertEqual(self.lab.profiles()['old']['password'],'working')
        self.assertEqual(self.lab.profiles()['old']['sudo_password'],'sudo-secret')
        self.assertEqual(self.lab.hosts()[0]['ip'],'10.1.0.50')

    def test_offline_cannot_replace_credentials(self):
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','password':'working'})
        for update in ({'password':'wrong'},{'sudo_password':'wrong'},{'clear_password':True}):
            data=dict(name='old',ip='10.1.0.50',user='lab',save_offline=True,**update)
            self.assertEqual(self.client.post('/api/connections',json=data).status_code,400)
        self.assertEqual(self.lab.profiles()['old']['password'],'working')
        self.assertEqual(self.lab.hosts()[0]['ip'],'10.0.0.1')

    def test_validation(self):
        for data in [{'name':'bad\nname','ip':'10.0.0.1','user':'lab'}, {'name':'safe','ip':'address ansible_connection=local','user':'lab'}, {'name':'safe','ip':'10.0.0.1','user':'lab','port':99999}]:
            self.assertEqual(self.client.post('/api/connections', json=data).status_code,400)
        self.assertEqual(self.client.post('/api/operate',json={'hosts':['all'],'action':'facts'}).status_code,400)
        self.assertEqual(self.client.post('/api/connections',json={},headers={'Origin':'http://evil.test'}).status_code,403)

    def test_network_and_auth_states(self):
        host = self.lab.hosts()[0]
        with patch('dashboard.lab.socket.create_connection', side_effect=TimeoutError()):
            self.assertEqual(self.lab.check(host)['state'],'unreachable')
        with patch('dashboard.lab.socket.create_connection'), patch.object(self.lab,'execute', return_value=(False,'Authentication failed')):
            result = self.lab.check(host)
            self.assertTrue(result['reachable'])
            self.assertFalse(result['ready'])
            self.assertEqual(result['state'],'needs_setup')
        with patch('dashboard.lab.socket.create_connection'), patch.object(self.lab,'execute', return_value=(True,'pong')):
            self.assertEqual(self.lab.check(host)['state'],'ready')

    def test_fleet_connectivity_does_not_require_authentication(self):
        with patch('dashboard.lab.socket.create_connection'), patch.object(self.lab,'execute') as execute:
            response=self.client.post('/api/check',json={'hosts':['old'],'connectivity_only':True})
        self.assertTrue(response.json['statuses']['old']['reachable'])
        self.assertFalse(response.json['statuses']['old']['ready'])
        execute.assert_not_called()
        with patch('dashboard.lab.socket.create_connection',side_effect=TimeoutError()):
            response=self.client.post('/api/check',json={'hosts':['old'],'connectivity_only':True})
        self.assertFalse(response.json['statuses']['old']['reachable'])

    def test_mixed_platform_operations(self):
        with patch.object(self.lab,'execute',return_value=(True,'ok')) as execute:
            result = self.client.post('/api/operate',json={'hosts':['old','win'],'action':'facts'})
            self.assertTrue(result.json['success'])
            modules = {call.args[1] for call in execute.call_args_list}
            self.assertEqual(modules, {'ansible.builtin.setup','ansible.windows.setup'})
        with patch.object(self.lab,'execute',return_value=(True,'ok')) as execute:
            self.client.post('/api/operate',json={'hosts':['old'],'action':'command','value':'hostname'})
            self.assertFalse(execute.call_args.kwargs['timeout'] == 0)
            self.assertFalse(execute.call_args.args[3])

    def test_password_survives_reload_and_blank_save(self):
        password = ' spaces !"$`\\ '
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','password':password})
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','password':''})
        self.assertEqual(Lab(self.root).profiles()['old']['password'],password)
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','password':'replacement'})
        self.assertEqual(self.lab.profiles()['old']['password'],'replacement')
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','clear_password':True})
        self.assertFalse(self.lab.hosts()[0]['has_password'])

    def test_all_operation_modules_and_privileges(self):
        expected = {
            'ping': ('ansible.builtin.ping', 'ansible.windows.win_ping'),
            'facts': ('ansible.builtin.setup', 'ansible.windows.setup'),
            'install': ('ansible.builtin.package', 'chocolatey.chocolatey.win_chocolatey'),
            'command': ('ansible.builtin.shell', 'ansible.windows.win_shell'),
            'restart': ('ansible.builtin.reboot', 'ansible.windows.win_reboot'),
            'shutdown': ('ansible.builtin.shell', 'ansible.windows.win_shell'),
        }
        for action, modules in expected.items():
            with self.subTest(action=action), patch.object(self.lab,'execute',return_value=(True,'ok')) as execute:
                response = self.client.post('/api/operate',json={'hosts':['old','win'],'action':action,'value':'curl' if action=='install' else 'hostname'})
                self.assertTrue(response.json['success'])
                calls = {call.args[0]['name']:call for call in execute.call_args_list}
                self.assertEqual(calls['old'].args[1],modules[0])
                self.assertEqual(calls['win'].args[1],modules[1])
                self.assertEqual(calls['old'].args[3],action in ('install','restart','shutdown'))
                self.assertFalse(calls['win'].args[3])

    def test_partial_operation_failure(self):
        def mixed(host,*args,**kwargs):
            return host['name']=='old', 'test output'
        with patch.object(self.lab,'execute',side_effect=mixed):
            response=self.client.post('/api/operate',json={'hosts':['old','win'],'action':'facts'})
            self.assertFalse(response.json['success'])
            self.assertEqual({r['host']:r['success'] for r in response.json['results']},{'old':True,'win':False})

    def test_timeout_cleans_up_credentials(self):
        from subprocess import TimeoutExpired
        paths=[]
        def timeout(cmd,**kwargs):
            paths.append(Path(cmd[cmd.index('-e')+1][1:]))
            raise TimeoutExpired(cmd,20)
        with patch('dashboard.lab.subprocess.run',side_effect=timeout):
            ok, output=self.lab.execute(self.lab.hosts()[0],'ansible.builtin.ping')
        self.assertFalse(ok)
        self.assertIn('timed out',output)
        self.assertFalse(paths[0].exists())

    def test_extra_vars_private_and_cleaned(self):
        self.lab.save({'name':'old','ip':'10.0.0.1','user':'lab','os':'linux','password':'private-secret'})
        seen = []
        def run(cmd, **kwargs):
            inventory = Path(cmd[cmd.index('-i')+1])
            self.assertNotEqual(inventory,self.lab.inventory)
            self.assertEqual(inventory.stat().st_mode & 0o777,0o600)
            self.assertIn('old',inventory.read_text())
            seen.append(inventory)
            path = Path(cmd[cmd.index('-e')+1][1:])
            seen.append(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            variables=json.loads(path.read_text())
            self.assertEqual(variables['ansible_password'],'private-secret')
            self.assertEqual(variables['ansible_host'],'10.0.0.1')
            self.assertEqual(variables['ansible_user'],'lab')
            self.assertNotIn('private-secret',' '.join(cmd))
            from subprocess import CompletedProcess
            return CompletedProcess(cmd,0,'private-secret','')
        with patch('dashboard.lab.subprocess.run',side_effect=run):
            ok, output = self.lab.execute(self.lab.hosts()[0],'ansible.builtin.ping')
        self.assertTrue(ok)
        self.assertEqual(output,'[redacted]')
        self.assertFalse(seen[0].exists())
        self.assertTrue(all(not path.exists() for path in seen))

    def test_untouched_hosts_keep_legacy_inventory(self):
        from subprocess import CompletedProcess
        def run(cmd,**kwargs):
            self.assertEqual(Path(cmd[cmd.index('-i')+1]),self.lab.inventory)
            return CompletedProcess(cmd,0,'ok','')
        with patch('dashboard.lab.subprocess.run',side_effect=run):
            self.assertTrue(self.lab.execute(self.lab.hosts()[0],'ansible.builtin.ping')[0])


if __name__ == '__main__':
    unittest.main()
