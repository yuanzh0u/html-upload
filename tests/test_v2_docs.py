import argparse
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import client
import docsctl
import local

PHRASES = ('docs-api v2', '主版本', '目标 100–300 字', '硬上限 1000 字', '用户给了摘要就用用户的',
           '不要照抄页面开头的导航、标题、目录或样板文字', '不要写入 Key、手机号等敏感信息',
           '用户给了标签就原样使用', '用户说不要标签就不加', '先运行 `tags` 查看已用标签', '自选 1–3 个简短标签',
           '优先沿用已有标签', '已有文档的标签只在用户要求时才改',
           '把 report 原样转述给用户', '不要把部分成功说成全部成功', '用 replace，不要另传一篇',
           '先不带 --yes 预览', '得到用户明确同意后才加 --yes', '只有用户确认要另起一篇时才加 --new',
           '不要绕过本工具直接请求接口')


def read(path):
    return path.read_text(encoding='utf-8')


class DocTests(unittest.TestCase):
    def setUp(self):
        self.skill = read(ROOT / 'SKILL.md')
        self.refs = {p.name: read(p) for p in (ROOT / 'references').glob('*.md')}

    def test_skill_frontmatter(self):
        self.assertTrue(self.skill.startswith('---\nname: html-upload\ndescription: '))

    def test_skill_required_phrases(self):
        for phrase in PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.skill)

    def test_old_tag_rule_is_gone(self):                            # D65、重点关注 5
        requirements = read(ROOT / 'docs' / 'V2-requirements.md')
        for text, old in ((self.skill, '只用用户给出的标签'), (self.refs['commands.md'], '不表示可自动替用户选标签'),
                          (requirements, '技能不得自行编造标签')):
            with self.subTest(old=old):
                self.assertNotIn(old, text)
        self.assertIn('上传前用它查看已用标签', self.refs['commands.md'])
        self.assertIn('自选 1–3 个', requirements)

    def test_every_subcommand_documented(self):
        parser = docsctl.build_parser()
        action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertEqual(set(action.choices), {'me', 'upload', 'replace', 'resume', 'list', 'get', 'edit',
                                               'favorite', 'unfavorite', 'delete', 'tags'})
        for name in action.choices:
            with self.subTest(name=name):
                self.assertIn(f'docsctl.py {name}', self.skill)
                self.assertIn(f'## {name}', self.refs['commands.md'])

    def test_every_error_code_documented(self):                     # C-FR-08
        for code in client.ADVICE:
            with self.subTest(code=code):
                self.assertIn(f'`{code}`', self.refs['errors.md'])

    def test_exit_codes_documented(self):
        for code in ('0', '2', '3', '4', '5'):
            with self.subTest(code=code):
                self.assertRegex(self.skill, rf'\|\s*{code}\s*\|')

    def test_no_v1_leftovers(self):
        texts = {'SKILL.md': self.skill, **self.refs, **{p.name: read(p) for p in (ROOT / 'bin').glob('*.py')}}
        for name, text in texts.items():
            for word in ('taxonomy', '/console/', 'upload.py', '/api/v1', 'confirm-prompt'):
                with self.subTest(file=name, word=word):
                    self.assertNotIn(word, text)
        for text in (self.refs['credentials-posix.md'], local.KEY_GUIDANCE):
            self.assertIn('https://docs.zh0uyuan.com/keys', text)
