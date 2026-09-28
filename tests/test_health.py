import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('health', Path(__file__).parents[1] / 'scripts/health.py')
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


class HealthTests(unittest.TestCase):
    def test_linux_probe(self):
        result = subprocess.run(['sh', 'scripts/health.sh'], capture_output=True, text=True, check=True)
        for section in ('Uptime', 'Memory usage', 'CPU usage', 'Top 5 live processes'):
            self.assertIn(section, result.stdout)
        process_lines = result.stdout.split('includes sleeping processes)\n')[1].splitlines()
        self.assertLessEqual(len(process_lines), 6)
        self.assertIn('PID', process_lines[0])

    @patch.dict(os.environ, {'HEALTH_SECRETS_JSON': '{"TOKEN": "account-token", "OTHER": "unrelated"}'})
    def test_codespace_token_isolation(self):
        with patch.object(health, 'execute', return_value='healthy') as execute:
            health.collect('github-codespace', {'name': 'example-space'},
                           {'github_token': 'secret:TOKEN'}, health.Credentials(), 'sh -s', 'probe', 5)
            argv, env, probe, timeout = execute.call_args.args
            self.assertEqual(argv[:6], ['gh', 'cs', 'ssh', '-c', 'example-space', '--'])
            self.assertEqual(env['GH_TOKEN'], 'account-token')
            self.assertNotIn('HEALTH_SECRETS_JSON', env)
            self.assertNotIn('unrelated', env.values())

    @patch.dict(os.environ, {'KEY': 'private-key', 'PASSWORD': 'secret-password', 'HOSTS': 'host ssh-ed25519 key'})
    def test_ssh_credentials_and_host_verification(self):
        paths = []
        def inspect(argv, env, probe, timeout):
            self.assertEqual(argv[:2], ['sshpass', '-e'])
            self.assertEqual(env['SSHPASS'], 'secret-password')
            self.assertNotIn('secret-password', argv)
            self.assertIn('StrictHostKeyChecking=yes', argv)
            key = Path(argv[argv.index('-i') + 1])
            paths.append(key)
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.read_text(), 'private-key\n')
            return 'ok'
        with patch.object(health, 'execute', side_effect=inspect):
            health.collect('ssh', {'host': 'host', 'user': 'root', 'privatekey': 'env:KEY',
                           'password': 'env:PASSWORD', 'known_hosts': 'env:HOSTS'}, {},
                           health.Credentials(), 'sh -s', 'probe', 5)
        self.assertFalse(paths[0].exists())

    def test_timeout(self):
        with self.assertRaises(TimeoutError):
            health.execute(['sh', '-c', 'sleep 10'], health.child_env(), '', 0.05)

    @patch.dict(os.environ, {'HEALTH_SECRETS_JSON': '{"TOKEN": "hidden-token"}'})
    def test_partial_failure_and_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'probe.sh').write_text('echo healthy')
            (root / 'config.yaml').write_text('''probe: probe.sh
servers:
  ssh:
    broken: {host: one}
    healthy: {host: two}
''')
            with patch.object(health, 'collect', side_effect=[RuntimeError('failure'), 'ok hidden-token']):
                report = health.run(root / 'config.yaml', root / 'out')
            self.assertEqual(report['failed'], 1)
            self.assertEqual(len(report['results']), 2)
            self.assertEqual(report['results'][1]['status'], 'ok')
            for file in (root / 'out').iterdir():
                self.assertNotIn('hidden-token', file.read_text())
            json.loads((root / 'out/health.json').read_text())

    def test_missing_secret_becomes_target_error(self):
        with patch.dict(os.environ, {'HEALTH_SECRETS_JSON': '{}'}):
            with self.assertRaises(health.ConfigError):
                health.Credentials().resolve('secret:ABSENT')

    def test_bad_yaml_still_writes_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'bad.yaml').write_text('servers: [')
            report = health.run(root / 'bad.yaml', root / 'out')
            self.assertEqual(report['failed'], 1)
            self.assertTrue((root / 'out/health.md').exists())

    def test_workflow_and_sample_config(self):
        import yaml
        # BaseLoader preserves YAML 1.2's 'on' key without YAML 1.1 coercion.
        workflow = yaml.load(Path('.github/workflows/health.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow['on']['schedule'][0]['cron'], '17 * * * *')
        self.assertIn('workflow_dispatch', workflow['on'])
        self.assertEqual(workflow['permissions']['contents'], 'read')
        self.assertEqual(workflow['jobs']['publish']['permissions']['contents'], 'write')
        self.assertNotIn('toJSON(secrets)', Path('.github/workflows/health.yml').read_text())
        self.assertNotIn('toJSON(vars)', Path('.github/workflows/health.yml').read_text())
        self.assertIn('FROM alpine:3.23', Path('.github/Dockerfile.health').read_text())
        config = yaml.safe_load(Path('servers.yaml.example').read_text())
        self.assertEqual(list(health.targets(config)), [])


if __name__ == '__main__':
    unittest.main()
