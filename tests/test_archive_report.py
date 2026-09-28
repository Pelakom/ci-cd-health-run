import base64
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
import archive_report


def error(code):
    exc = HTTPError('https://api.github.com/test', code, 'test', {}, None)
    exc.close()
    return exc


class ArchiveTests(unittest.TestCase):
    def invoke(self, root):
        path = root / 'health.md'
        path.write_text('# Health\n')
        return archive_report.archive(path, 'owner/repo', 'main', '123', '1', 'test-token', 'https://api.github.com')

    def test_create_single_file_without_overwrite(self):
        with tempfile.TemporaryDirectory() as d, patch.object(archive_report, 'request', side_effect=[error(404), {}]) as req:
            self.assertEqual(self.invoke(Path(d)), 'reports-sample/health-123-1.md')
            payload = req.call_args.args[3]
            self.assertEqual(payload['branch'], 'main')
            self.assertEqual(base64.b64decode(payload['content']), b'# Health\n')
            self.assertNotIn('sha', payload)

    def test_existing_identical_report_is_noop(self):
        with tempfile.TemporaryDirectory() as d, patch.object(archive_report, 'request', return_value={'content': base64.b64encode(b'# Health\n').decode()}) as req:
            self.invoke(Path(d))
            self.assertEqual(req.call_count, 1)

    def test_existing_different_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as d, patch.object(archive_report, 'request', return_value={'content': 'b3RoZXI='}) as req:
            with self.assertRaises(ValueError):
                self.invoke(Path(d))
            self.assertEqual(req.call_count, 1)

    def test_conflict_retries_and_permission_error_does_not(self):
        with tempfile.TemporaryDirectory() as d, patch.object(archive_report, 'request', side_effect=[error(404), error(409), error(404), {}]) as req, patch.object(archive_report.time, 'sleep'):
            self.invoke(Path(d))
            self.assertEqual(req.call_count, 4)
        with tempfile.TemporaryDirectory() as d, patch.object(archive_report, 'request', side_effect=error(403)) as req:
            with self.assertRaises(HTTPError):
                self.invoke(Path(d))
            self.assertEqual(req.call_count, 1)
