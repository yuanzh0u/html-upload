"""与 docs-service V2 的本机联调：真实 HTTP、真实服务端代码、临时数据库；不连外网，不改 docs-service。"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

HERE = Path(__file__).resolve().parent
SERVICE = Path(os.environ.get('DOCS_SERVICE_DIR', HERE.parents[1] / 'docs-service'))
sys.path.insert(0, str(HERE.parent / 'bin'))
import docsctl

SKIP = None
_previous_bytecode_flag = sys.dont_write_bytecode
try:
    sys.dont_write_bytecode = True
    sys.path.append(str(SERVICE))
    from app.api_v2 import AgentAPI
    from app.services import apikeys, sharing
    from app.services.storage import MIN_FREE_BYTES
    spec = importlib.util.spec_from_file_location('docs_service_v2_support', SERVICE / 'tests/v2_support.py')
    support = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(support)
except Exception as exc:                                    # docs-service 还没有 V2：整组跳过
    SKIP = f'docs-service V2 不可用：{type(exc).__name__}'
finally:
    sys.dont_write_bytecode = _previous_bytecode_flag


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipIf(SKIP, SKIP)
class IntegrationTests(unittest.TestCase):
    def setUp(self):
        # Registered first so restoration runs after server shutdown and cleanup.
        previous_bytecode_flag = sys.dont_write_bytecode
        self.addCleanup(setattr, sys, 'dont_write_bytecode', previous_bytecode_flag)
        sys.dont_write_bytecode = True
        connect = support.file_db(self)
        self.root = support.new_storage(self)
        self.conn = connect()
        self.addCleanup(self.conn.close)
        self.me = support.add_user(self.conn, '甲')
        friend = support.add_user(self.conn, '乙')
        sharing.add_friend(self.conn, self.me, friend, support.T0)
        group = sharing.create_group(self.conn, self.me, '测试组', support.T0)
        sharing.add_member(self.conn, self.me, group, friend, support.T0)
        self.key = apikeys.generate(self.conn, self.me, support.T0)
        try:
            server = make_server('127.0.0.1', 0, None, handler_class=QuietHandler)
        except OSError:
            self.skipTest('本环境不允许回环 socket')
        self.addCleanup(server.server_close)
        self.origin = f'http://127.0.0.1:{server.server_port}'
        server.set_app(AgentAPI(connect, storage_root=self.root, origin=self.origin, clock=support.Clock(),
                                free_bytes=lambda: MIN_FREE_BYTES))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)

    def cli(self, *argv, key=None):
        out = io.StringIO()
        env = {'HOME': str(self.home), 'DOCS_API_KEY': key or self.key, 'DOCS_ORIGIN': self.origin}
        code = docsctl.run(list(argv), env=env, out=out)
        return code, json.loads(out.getvalue())

    def page(self, name, title, body='x'):
        path = self.home / name
        path.write_text(f'<!doctype html><html><head><meta charset="utf-8"><title>{title}</title></head>'
                        f'<body>{body}</body></html>', encoding='utf-8')
        return str(path)

    def test_full_flow(self):                                       # 契约 §4 的 10 个接口逐个走一遍
        self.assertEqual(self.cli('me')[1]['data']['api_version'].split('.')[0], '2')
        args = ('upload', self.page('a.html', '周报'), '--summary', '本周进展。', '--tag', '周报',
                '--share-friend', '乙', '--share-friend', '丁', '--share-group', '测试组', '--share-group', '接口组')
        code, result = self.cli(*args)
        doc = result['data']
        self.assertEqual((code, doc['url']), (0, f"{self.origin}/d/{doc['id']}"))
        for text in ('丁（不是你的好友）', '接口组（没有这个群组）'):
            self.assertIn(text, result['report'])
        self.assertEqual(self.cli(*args)[1]['data']['id'], doc['id'])                     # 重放：同一篇
        self.assertEqual([i['id'] for i in self.cli('list', '--tag', '周报')[1]['data']['items']], [doc['id']])
        edited = self.cli('edit', doc['id'], '--title', '新标题', '--tag', '月报')[1]['data']
        self.assertEqual((edited['title'], edited['tags']), ('新标题', ['月报']))
        self.assertIsNone(self.cli('favorite', doc['id'])[1]['data']['expires_at'])
        self.assertIsNotNone(self.cli('unfavorite', doc['id'])[1]['data']['expires_at'])
        code, result = self.cli('replace', doc['id'], self.page('b.html', '周报 v2', 'y'), '--summary', '更新后的摘要。')
        self.assertEqual((code, result['data']['id'], result['data']['url']), (0, doc['id'], doc['url']))
        self.assertIn({'tag': '月报', 'count': 1}, self.cli('tags')[1]['data']['items'])
        self.assertEqual(self.cli('delete', doc['id'])[0], 3)
        self.assertEqual(self.cli('delete', doc['id'], '--yes')[0], 0)
        self.assertEqual(self.cli('get', doc['id'])[0], 2)
        self.assertEqual(self.cli('me', key='x' * 24)[0], 2)

    def test_list_all_1000_with_duplicate_sort_keys(self):          # C-FR-06 验收、D55
        ids = {support.add_doc(self.conn, self.root, self.me, title=f'标题{i % 7}', uploaded_at=support.T0 + i // 10)
               for i in range(1000)}
        for sort in ('uploaded_desc', 'uploaded_asc', 'title'):
            with self.subTest(sort=sort):
                code, result = self.cli('list', '--all', '--limit', '37', '--sort', sort)
                got = [item['id'] for item in result['data']['items']]
                self.assertEqual((code, len(got), set(got)), (0, 1000, ids))
