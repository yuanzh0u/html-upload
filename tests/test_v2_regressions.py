"""Supplemental regressions beyond the frozen implementation-plan tests."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import fields


class FieldRegressionTests(unittest.TestCase):
    def test_blank_summary_message_and_nonblank_control_rejection(self):
        for blank in ('\r', '\v', '\f', ' \r\v\f\t\n '):
            with self.subTest(blank=repr(blank)):
                with self.assertRaises(fields.FieldError) as caught:
                    fields.clean_summary(blank)
                self.assertEqual(caught.exception.field, 'summary')
                self.assertEqual(str(caught.exception), '摘要不能为空')
        for nonblank in ('a\rb', '\va', 'b\f'):
            with self.subTest(nonblank=repr(nonblank)):
                with self.assertRaises(fields.FieldError) as caught:
                    fields.clean_summary(nonblank)
                self.assertEqual(caught.exception.field, 'summary')
                self.assertEqual(str(caught.exception), '摘要含不允许的控制字符')
