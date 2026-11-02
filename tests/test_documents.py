import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
class Documents(unittest.TestCase):
    def test_01_protocol(self):
        path = ROOT / 'references/protocol.md'
        self.assertTrue(path.is_file(), '协议文件行为尚未实现')
        for word in ('sha256', 'Idempotency-Key', 'docs-upload/v1', 'selectable_for_upload', '14046552'):
            self.assertIn(word, path.read_text())
    def test_02_confirmation(self):
        path = ROOT / 'references/confirmation.md'
        self.assertTrue(path.is_file(), '确认参考尚未实现')
        for word in ('existing_default', 'explicit', '非交互', '不自动', 'create_confirmed'):
            self.assertIn(word, path.read_text())
    def test_03_credentials(self):
        path = ROOT / 'references/credentials-posix.md'
        self.assertTrue(path.is_file(), 'POSIX 引导尚未实现')
        text = path.read_text()
        self.assertIn('IFS= read -rs', text)
        self.assertNotIn('read -rsp', text)
    def test_04_windows(self):
        path = ROOT / 'references/credentials-windows.md'
        self.assertTrue(path.is_file(), 'Windows 边界尚未实现')
        self.assertIn('verified: no', path.read_text())
        self.assertNotIn('icacls', path.read_text())
    def test_05_manifest(self):
        path = ROOT / 'references/manifest.md'
        self.assertTrue(path.is_file(), 'manifest 参考尚未实现')
        lines = [s for s in path.read_text().splitlines() if s.startswith('{')]
        self.assertEqual(len(lines), 2)
        for line in lines:
            value = json.loads(line)
            self.assertEqual(value['schema_version'], 1)
            self.assertIn(value['status'], ('prepared', 'uploaded'))
    def test_06_external(self):
        for name in ('references/external-links.md', 'templates/confirm-prompt.txt'):
            path = ROOT / name
            self.assertTrue(path.is_file(), '外链文案尚未实现')
            self.assertIn('不拒绝上传', path.read_text())
            self.assertIn('外链提示：未发现', path.read_text())
    def test_07_report(self):
        for name in ('references/report.md', 'templates/report.txt'):
            path = ROOT / name
            self.assertTrue(path.is_file(), '报告模板尚未实现')
            for title in ('线上链接', 'docid', '本地副本', 'manifest', '待办'):
                self.assertIn(title, path.read_text())
