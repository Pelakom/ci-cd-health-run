import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import health
import workflow


class WorkflowTests(unittest.TestCase):
    def config(self, root):
        path = root / 'servers.yaml'
        path.write_text('''probe: probe.sh
servers:
  github-codespace:
    account:
      github_token: secret:MY_CHOSEN_TOKEN
      first: {name: first-space}
      second: {name: second-space}
      disabled: {enabled: false, name: disabled-space}
''')
        (root / 'probe.sh').write_text('echo test')
        return path

    def test_plan_keeps_dynamic_names_without_values(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ, {'MY_CHOSEN_TOKEN': 'not-in-plan'}):
                entries = workflow.plan(self.config(Path(d)))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[1]['id'], 1)
            self.assertEqual(entries[0]['references']['github_token'],
                             {'source': 'secret', 'name': 'MY_CHOSEN_TOKEN'})
            self.assertNotIn('not-in-plan', json.dumps(entries))

    def test_only_selected_target_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.object(health, 'collect', return_value='ok') as collect:
                report = health.run(self.config(root), root / 'reports', target_index=1)
            self.assertEqual(collect.call_count, 1)
            self.assertEqual(report['results'][0]['server'], 'account/second')

    @patch.dict(os.environ, {
        'HEALTH_SECRETS_JSON': '{}',
        'HEALTH_REFERENCES_JSON': '{"github_token":{"source":"secret","name":"CUSTOM"}}',
        'HEALTH_GITHUB_TOKEN': 'chosen-token', 'UNRELATED': 'other-token',
    })
    def test_target_scoped_credential_resolution(self):
        credentials = health.Credentials()
        self.assertEqual(credentials.resolve('secret:CUSTOM'), 'chosen-token')
        self.assertEqual(credentials.redact('chosen-token'), '[REDACTED]')
        with self.assertRaises(health.ConfigError):
            credentials.resolve('secret:UNRELATED')

    def test_missing_artifact_is_reported_and_valid_one_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            entries = workflow.plan(self.config(root))
            plan_path = root / 'plan.json'
            plan_path.write_text(json.dumps(entries))
            report_dir = root / 'artifacts/target-0'
            report_dir.mkdir(parents=True)
            (report_dir / 'health.json').write_text(json.dumps({'results': [
                {'type': 'github-codespace', 'server': 'account/first', 'status': 'ok', 'output': 'healthy'}]}))
            report = workflow.merge(plan_path, root / 'artifacts', root / 'reports')
            self.assertEqual(report['failed'], 1)
            self.assertEqual(report['results'][0]['output'], 'healthy')
            self.assertEqual(report['results'][1]['status'], 'error')

    def test_empty_inventory(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            path = root / 'plan.json'
            path.write_text('[]')
            report = workflow.merge(path, root / 'artifacts', root / 'reports')
            self.assertEqual(report['results'], [])
            self.assertIn('No enabled targets', (root / 'reports/health.md').read_text())


if __name__ == '__main__':
    unittest.main()
