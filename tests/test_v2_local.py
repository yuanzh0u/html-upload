import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import local
from local import Stop

HTML = ('<!doctype html><html><head><meta charset="utf-8"><title>周报</title></head>'
        '<body>x</body></html>').encode()
KEY = 'k' * 24


class LocalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)

    def write(self, name, data):
        path = self.home / name
        path.write_bytes(data)
        return path

    def key_file(self, text, file_mode=0o600, dir_mode=0o700):
        folder = self.home / '.config/docs'
        folder.mkdir(parents=True)
        (folder / 'api-key').write_text(text)
        (folder / 'api-key').chmod(file_mode)
        folder.chmod(dir_mode)

    def test_origin_rules(self):
        self.assertEqual(local.origin_from({}), 'https://docs.zh0uyuan.com')
        for ok in ('http://127.0.0.1:8080', 'https://localhost'):
            self.assertEqual(local.origin_from({'DOCS_ORIGIN': ok}), ok)
        for bad in ('https://evil.example', 'http://127.0.0.1:0', 'http://127.0.0.1.evil.com'):
            with self.subTest(bad=bad), self.assertRaises(Stop):
                local.origin_from({'DOCS_ORIGIN': bad})

    def test_credential_sources(self):                              # C-FR-07
        self.assertEqual(local.credential({'DOCS_API_KEY': KEY + '\r\n'}, self.home), KEY)
        with self.assertRaises(Stop) as caught:
            local.credential({'DOCS_API_KEY': ''}, self.home)      # 已设置为空也不回退到文件
        self.assertIn('API Key', str(caught.exception))
        self.key_file(KEY)
        self.assertEqual(local.credential({}, self.home), KEY)

    def test_credential_file_permissions(self):
        self.key_file(KEY, file_mode=0o644)
        with self.assertRaises(Stop) as caught:
            local.credential({}, self.home)
        self.assertNotIn(KEY, str(caught.exception))

    def test_read_html_limits(self):                                # C-FR-03
        self.assertEqual(local.read_html(self.write('a.html', HTML)), HTML)
        exact = b'<html>' + b'a' * (2_097_152 - 13) + b'</html>'
        self.assertEqual(len(local.read_html(self.write('b.html', exact))), 2_097_152)
        for name, data in (('c.html', exact + b'a'), ('d.txt', HTML)):
            with self.subTest(name=name), self.assertRaises(Stop):
                local.read_html(self.write(name, data))
        link = self.home / 'e.html'
        link.symlink_to(self.home / 'a.html')
        with self.assertRaises(Stop):
            local.read_html(link)

    def test_inspect_rejects_bad_bytes(self):
        for bad in (b'\xff' + HTML, b'\xef\xbb\xbf' + HTML, HTML.replace(b'>x<', b'>\x00<'), b'hello',
                    HTML.replace(b'</html>', b''), HTML.replace(b'utf-8', b'gbk')):
            with self.subTest(bad=bad[:12]), self.assertRaises(Stop):
                local.inspect_html(bad)

    def test_title_selection(self):
        self.assertEqual(local.pick_title(None, local.inspect_html(HTML)), '周报')
        self.assertEqual(local.pick_title(' 新标题 ', local.inspect_html(HTML)), '新标题')
        for data in (HTML.replace(b'</head>', b'<title>B</title></head>'), HTML.replace('<title>周报</title>'.encode(), b'')):
            with self.assertRaises(Stop) as caught:
                local.pick_title(None, local.inspect_html(data))
            self.assertIn('--title', str(caught.exception))

    def test_source_info_from_studio_meta(self):
        page = HTML.replace(b'</head>', b'<meta name="hx:generator" content="html-studio">'
                                        b'<meta name="hx:generated-at" content="2027-01-15T08:00:00+08:00"></head>')
        self.assertEqual(local.build_source_info(local.inspect_html(page), Path('/x/report.html')),
                         {'client': 'html-upload/2', 'file': 'report.html', 'generator': 'html-studio',
                          'generated_at': '2027-01-15T08:00:00+08:00'})

    def test_source_info_without_meta(self):
        self.assertEqual(local.build_source_info(local.inspect_html(HTML), Path('a.html')),
                         {'client': 'html-upload/2', 'file': 'a.html'})
