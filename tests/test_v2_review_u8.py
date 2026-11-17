"""U8: page titles exclude foreign content and honor studio metadata."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import docsctl
import local
from v2_review_support import LoopbackDocs


def page(contents):
    return ('<!doctype html><html>' + contents + '</html>').encode('utf-8')


class PageTitleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        env = patch.dict(os.environ, {'HOME': temporary.name,
                                     'DOCS_API_KEY': 'review-u8-fake-key-00000000',
                                     'DOCS_ORIGIN': 'http://127.0.0.1:9'})
        env.start()
        self.addCleanup(env.stop)

    def selected_title(self, explicit, inspected):
        try:
            return local.pick_title(explicit, inspected)
        except local.Stop as error:
            self.fail('Unexpected title rejection: ' + str(error))

    def test_svg_and_math_titles_do_not_compete_with_page_title(self):
        for tag in ('svg', 'math', 'SVG', 'MATH'):
            with self.subTest(tag=tag):
                inspected = local.inspect_html(page('<title>页面标题</title>'
                    f'<{tag}><title>图形标题</title></{tag}>'))
                self.assertEqual(inspected['titles'], ['页面标题'])
                self.assertEqual(self.selected_title(None, inspected), '页面标题')

    def test_foreign_titles_alone_cannot_supply_page_title(self):
        for tag in ('svg', 'math'):
            with self.subTest(tag=tag):
                with self.assertRaises(local.Stop) as caught:
                    local.pick_title(None, local.inspect_html(page(
                        f'<{tag}><title>图形标题</title></{tag}>')))
                self.assertIn('--title', str(caught.exception))

    def test_hx_title_precedes_single_page_title_and_supplies_missing_title(self):
        for html_title in ('', '<title>页面标题</title>'):
            with self.subTest(html_title=html_title):
                inspected = local.inspect_html(page(html_title +
                    '<meta name="hx:title" content=" Studio &amp; 标题 ">'))
                self.assertEqual(self.selected_title(None, inspected), 'Studio & 标题')

    def test_multiple_page_titles_require_explicit_title_even_equal_or_with_meta(self):
        for second in ('相同标题', '不同标题'):
            for meta in ('', '<meta name="hx:title" content="元数据标题">'):
                with self.subTest(second=second, meta=meta):
                    inspected = local.inspect_html(page('<title>相同标题</title>' + meta +
                        '<title>' + second + '</title>'))
                    with self.assertRaises(local.Stop) as caught:
                        local.pick_title(None, inspected)
                    self.assertIn('--title', str(caught.exception))

    def test_explicit_title_overrides_metadata_and_page_title_ambiguity(self):
        for titles in ('', '<title>页面</title>', '<title>相同</title><title>相同</title>',
                       '<title>甲</title><title>乙</title>'):
            with self.subTest(titles=titles):
                inspected = local.inspect_html(page(titles +
                    '<meta name="hx:title" content="元数据标题"><math><title>图题</title></math>'))
                self.assertEqual(self.selected_title(' 显式标题 ', inspected), '显式标题')

    def test_nested_and_self_closing_foreign_elements_preserve_later_page_title(self):
        fixtures = (
            '<svg/><math/><title>页面标题</title>',
            '<svg><svg/><title>外层图题</title></svg><title>页面标题</title>',
            '<math><math/><title>外层公式题</title></math><title>页面标题</title>',
            '<svg><g><math><title>内层题</title></math><title>外层题</title></g></svg>'
            '<title>页面标题</title>',
            '<math><svg><title>内层题</title></svg><title>外层题</title></math>'
            '<title>页面标题</title>',
            '<svg><title/><g/><title>图题</title></svg><title>页面标题</title>',
        )
        for html in fixtures:
            with self.subTest(html=html):
                inspected = local.inspect_html(page(html))
                self.assertEqual(self.selected_title(None, inspected), '页面标题')

    def test_script_and_template_exclusion_survives_foreign_content(self):
        inspected = local.inspect_html(page(
            '<template><svg><title>模板图题</title></svg><title>模板页题</title></template>'
            '<script>"<svg><title>脚本图题</title></svg>"</script>'
            '<svg><template><title>图中模板</title></template>'
            '<script>"<title>图中脚本</title>"</script><title>图题</title></svg>'
            '<title>真实页面</title>'))
        self.assertEqual(self.selected_title(None, inspected), '真实页面')

    def test_invalid_hx_title_requires_explicit_override_without_fallback(self):
        for content in ('', '   ', '含\u200b隐藏字'):
            with self.subTest(content=content):
                inspected = local.inspect_html(page('<title>合法页面标题</title>'
                    '<meta name="hx:title" content="' + content + '">'))
                with self.assertRaises(local.Stop) as caught:
                    local.pick_title(None, inspected)
                self.assertIn('--title', str(caught.exception))
                self.assertEqual(self.selected_title('显式标题', inspected), '显式标题')


class TitleUploadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.path = self.home / 'studio.html'
        self.site = LoopbackDocs(self)

    def upload(self, raw, *extra):
        self.path.write_bytes(raw)
        out = io.StringIO()
        code = docsctl.run(['upload', str(self.path), '--summary', '隔离标题测试', *extra],
            env={'HOME': str(self.home), 'DOCS_API_KEY': self.site.fake.key,
                 'DOCS_ORIGIN': self.site.origin}, out=out, clock=lambda: 1_800_000_000,
            sleep=lambda _: None, rng=lambda a, b: 0)
        result = json.loads(out.getvalue())
        self.assertEqual(result['exit_code'], code)
        self.assertNotIn(self.site.fake.key, out.getvalue())
        return code, result

    def test_upload_uses_hx_title_preserves_html_and_source_info_taxonomy(self):
        raw = page('<title>页面标题</title><meta name="hx:title" content="Studio 标题">'
                   '<meta name="hx:generator" content="html-studio">'
                   '<svg><title>图形标题</title></svg><math><title>公式标题</title></math>')
        code, result = self.upload(raw)
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['title'], 'Studio 标题')
        sent = json.loads(next(call['body'] for call in self.site.fake.calls
                               if call['method'] == 'POST'))
        self.assertEqual(sent['title'], 'Studio 标题')
        self.assertEqual(base64.b64decode(sent['html_base64']), raw)
        self.assertEqual(sent['source_info'], {'client': 'html-upload/2', 'file': 'studio.html',
                                               'generator': 'html-studio'})
        self.assertEqual(self.path.read_bytes(), raw)

    def test_duplicate_page_titles_with_metadata_reject_before_network_or_archive(self):
        code, result = self.upload(page('<title>相同</title><title>相同</title>'
                                       '<meta name="hx:title" content="元数据标题">'))
        self.assertEqual(code, 2)
        self.assertIn('--title', result['report'])
        self.assertEqual(self.site.fake.calls, [])
        self.assertFalse((self.home / 'docs').exists())

    def test_explicit_upload_title_overrides_duplicate_titles_and_metadata(self):
        code, result = self.upload(page('<title>相同</title><title>相同</title>'
                                       '<meta name="hx:title" content="元数据标题">'),
                                   '--title', '明确指定')
        self.assertEqual(code, 0)
        self.assertEqual(result['data']['title'], '明确指定')


if __name__ == '__main__':
    unittest.main()
