import base64
import hashlib
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import store
from local import Stop

DATA = b'<!doctype html><html><title>t</title></html>'
SHA = hashlib.sha256(DATA).hexdigest()
RAW = json.dumps({'title': 't', 'summary': 's', 'html_base64': base64.b64encode(DATA).decode(),
                  'sha256': SHA}).encode()
T0 = 1_800_000_000.0
DOC = {'id': 'a' * 32, 'url': 'http://127.0.0.1:9/d/' + 'a' * 32, 'title': 't'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.root = self.home / 'docs/v2'

    def manifest(self):
        return [json.loads(line) for line in (self.root / 'manifest.jsonl').read_text().splitlines()]

    def mode(self, path):
        return stat.S_IMODE(path.lstat().st_mode)

    def prepare(self):
        return store.prepare(self.home, 'upload', None, RAW, SHA, 'idem-1', T0, title='t')

    def test_prepare_writes_private_files_and_manifest(self):
        meta = self.prepare()
        self.assertEqual((self.mode(self.root), self.mode(self.root / '.pending')), (0o700, 0o700))
        for suffix in ('.request.json', '.meta.json'):
            self.assertEqual(self.mode(self.root / '.pending' / (meta['client_upload_id'] + suffix)), 0o600)
        row = self.manifest()[-1]
        self.assertEqual((row['schema_version'], row['status'], row['op'], row['sha256']),
                         (2, 'prepared', 'upload', SHA))
        self.assertNotIn('idem-1', (self.root / 'manifest.jsonl').read_text())

    def test_prepare_refuses_symlinked_root(self):
        (self.home / 'docs').mkdir()
        (self.home / 'docs/v2').symlink_to(self.home)
        with self.assertRaises(Stop) as caught:
            self.prepare()
        self.assertEqual(caught.exception.code, 5)

    def test_finalize_records_and_clears_pending(self):             # C-FR-03、C-FR-06
        meta = self.prepare()
        store.finalize(self.home, meta, T0 + 1, doc=DOC)
        row = self.manifest()[-1]
        self.assertEqual((row['status'], row['doc_id'], row['url'], row['sha256']),
                         ('uploaded', DOC['id'], DOC['url'], SHA))
        self.assertEqual(list((self.root / '.pending').iterdir()), [])
        self.assertEqual(list(self.root.rglob('*.html')), [])                   # 不留正文副本

    def test_load_pending_detects_tampering(self):
        meta = self.prepare()
        self.assertEqual(store.load_pending(self.home, meta['client_upload_id'])[1], RAW)
        request = self.root / '.pending' / (meta['client_upload_id'] + '.request.json')
        request.write_bytes(RAW.replace(base64.b64encode(DATA), base64.b64encode(DATA + b' ')))
        with self.assertRaises(Stop) as caught:
            store.load_pending(self.home, meta['client_upload_id'])
        self.assertEqual(caught.exception.code, 4)
