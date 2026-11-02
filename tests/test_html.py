import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from check_html import inspect_html

def page(extra='', title='Title'):
    return (f'<!doctype html><html><head><title>{title}</title><meta name="plans:completed" content="2026-10-15T12:00:00+08:00"><meta name="plans:worker" content="ExampleModel">{extra}</head></html>').encode()

class HTML(unittest.TestCase):
    def test_byte_rejections(self):
        for data in (b'', b'\xef\xbb\xbf'+page(), page()+b'\0', page()+b'\xff', b'<body></html>', b'<html>'):
            with self.subTest(size=len(data)), self.assertRaises(ValueError): inspect_html(data)
    def test_time(self):
        result = inspect_html(page())
        self.assertEqual(result['generated_at'], '2026-10-15T04:00:00Z')
        self.assertEqual(result['time_source'], 'html_meta')
    def test_conflicts(self):
        cases = [page('<meta name="plans:completed" content="2026-10-16">'), page().replace(b'2026-10-15T12:00:00+08:00', b'2026-10-15 12:00'), page('<title>Other</title>')]
        for data in cases: self.assertEqual(inspect_html(data)['status'], 'needs_clarification')
        self.assertEqual(inspect_html(page(), generated_at='2026-10-17T00:00:00Z')['status'], 'needs_clarification')
        self.assertEqual(inspect_html(page(), title='Other')['status'], 'needs_clarification')
    def test_template_script_and_comments(self):
        result = inspect_html(page('<template><title>Ignored</title><meta name="plans:completed" content="bad"></template><!-- <meta name="plans:completed" content="bad"> --><script>"<meta name=plans:worker content=bad>"</script>'))
        self.assertEqual(result['status'], 'ready')
    def test_fraction_and_equivalent_instants(self):
        result = inspect_html(page().replace(b'12:00:00+08:00', b'12:00:00.123456789+08:00'))
        self.assertEqual(result['generated_at'], '2026-10-15T04:00:00.123456789Z')
        self.assertEqual(inspect_html(page(), generated_at='2026-10-15T04:00:00Z')['status'], 'ready')
    def test_external(self):
        result = inspect_html(page('<script src="https://example.invalid/a.js"></script><style>@import "//font.invalid/a";p{background:url(https://x.invalid/i)}</style><img src="data:image/png;base64,AAAA"><img srcset="https://x.invalid/a 1x, //x.invalid/b 2x"><svg href="https://x.invalid/i"/>'))
        self.assertEqual(result['external_links']['total'], 5)
        self.assertIn('不拒绝上传', result['external_links']['notice'])
        self.assertIn('未发现', inspect_html(page('<img src="data:x">'))['external_links']['notice'])
    def test_charset_conflicts_are_invalid_html(self):
        for meta in ('<meta charset="gbk">', '<META CHARSET="UTF-16">', '<meta charset>',
                     '<meta charset="utf-8"><meta charset="windows-1252">',
                     '<meta http-equiv="Content-Type" content="text/html; charset=gbk">',
                     '<meta http-equiv="content-type" content="text/html; charset=&quot;utf-16&quot;">'):
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                inspect_html(page(meta))
    def test_utf8_and_inert_charset_declarations(self):
        for meta in ('<meta charset="UTF-8">', '<meta charset="utf8">',
                     '<meta http-equiv="Content-Type" content="text/html; CHARSET = utf-8">',
                     '<meta http-equiv="Content-Type" content="text/html">',
                     '<template><meta charset="gbk"></template>',
                     '<script>"<meta charset=gbk>"</script><!-- <meta charset="gbk"> -->'):
            with self.subTest(meta=meta):
                self.assertEqual(inspect_html(page(meta))['status'], 'ready')
    def test_external_urls_with_control_characters_are_not_collected(self):
        for control in ('\x1b', '\t', '\n', '\r', '\x7f', '\x85', '\u200b', '\u202e'):
            for bad in ('https://bad.invalid/a' + control + 'b', 'https://bad.invalid/a' + control):
                with self.subTest(url=repr(bad)):
                    result = inspect_html(page(f'<a href="{bad}">link</a><style>p{{background:url("{bad}")}}</style><img src="https://safe.invalid/image">'))
                    self.assertEqual(result['external_links']['urls'], ['https://safe.invalid/image'])
                    self.assertEqual(result['external_links']['total'], 1)
    def test_charset_duplicate_attributes_keep_the_first_value(self):
        for meta in ('<meta http-equiv="content-type" content="text/html;charset=gbk" content="text/html;charset=utf-8">',
                     '<meta http-equiv="content-type" http-equiv="x-ignore" content="text/html;charset=gbk">'):
            with self.subTest(meta=meta), self.assertRaises(ValueError):
                inspect_html(page(meta))
        for meta in ('<meta http-equiv="content-type" content="text/html;charset=utf-8" content="text/html;charset=gbk">',
                     '<meta http-equiv="x-ignore" http-equiv="content-type" content="text/html;charset=gbk">'):
            with self.subTest(meta=meta):
                try:
                    result = inspect_html(page(meta))
                except ValueError:
                    self.fail('an ignored duplicate attribute overrode the first charset value')
                self.assertEqual(result['status'], 'ready')
