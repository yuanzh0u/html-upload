"""Regression coverage for the P6 defect fixes; local fixtures only."""
import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import upload
from fake_server import server
from test_html import page


class P6Client(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.html = self.home / 'input.html'
        self.html.write_bytes(page())
        self.args = [str(self.html), '--project-name', 'Docs', '--type-name', 'Design', '--non-interactive']

    def invoke(self, state, *extra):
        out = io.StringIO()
        env = dict(HOME=str(self.home), DOCS_API_KEY='test-key-not-secret', DOCS_ORIGIN=state.origin)
        code = upload.run(self.args + list(extra), env=env, out=out, transport=state.transport,
                          sleep=lambda _: None)
        self.assertNotIn('test-key-not-secret', out.getvalue())
        return code, out.getvalue()

    def confirmation(self, plan, choices):
        file = self.home / (plan['client_upload_id'] + '.confirmation.json')
        file.write_text(json.dumps(dict(client_upload_id=plan['client_upload_id'],
                                        plan_sha256=plan['plan_sha256'], accepted=True, choices=choices)))
        return str(file)

    def add_project(self, state, suffix):
        state.tax['projects'].append(dict(id='project_' + suffix, name='Docs ' + suffix,
                                          category_id='cat_software', selectable_for_upload=True))

    def test_422_preserves_confirmed_title_time_and_project(self):
        with server() as state:
            self.add_project(state, 'v2')
            code, text = self.invoke(state, '--force-confirm')
            self.assertEqual(code, 3)
            file = self.confirmation(json.loads(text), {
                'project': {'action': 'reuse', 'id': 'project_v2'},
                'title': {'value': 'Confirmed title'},
                'generated_at': {'value': '2026-10-19 09:30', 'timezone': '+08:00'},
            })
            state.responses = [(422, dict(reason='taxonomy_confirmation_required',
                                          details=dict(items=[dict(field='model', candidates=[])])), {})]
            code, text = self.invoke(state, '--force-confirm', '--confirm-file', file)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            self.assertEqual(len(state.posts), 1)
            # A new whole-order accepted confirmation is still required.
            self.assertEqual(self.invoke(state, '--force-confirm')[0], 3)
            self.assertEqual(len(state.posts), 1)
            file = self.confirmation(plan, {'model': {'action': 'reuse', 'id': 'model_example'}})
            self.assertEqual(self.invoke(state, '--force-confirm', '--confirm-file', file)[0], 0)
            body = state.posts[-1]['body']
            self.assertEqual(body['project'], {'id': 'project_v2'})
            self.assertEqual(body['title'], 'Confirmed title')
            self.assertEqual(body['generated_at'], '2026-10-19T01:30:00Z')
            self.assertEqual(body['metadata_source'], {'time': 'confirmed', 'model': 'confirmed'})

    def test_422_invalidates_choices_for_the_changed_field(self):
        with server() as state:
            self.add_project(state, 'v2')
            self.add_project(state, 'v3')
            _, text = self.invoke(state, '--force-confirm')
            file = self.confirmation(json.loads(text), {'project': {'action': 'reuse', 'id': 'project_v2'}})
            state.responses = [(422, dict(reason='taxonomy_invalid',
                                          details=dict(field='project', successor_id='project_v3')), {})]
            code, text = self.invoke(state, '--force-confirm', '--confirm-file', file)
            self.assertEqual(code, 3)
            plan = json.loads(text)
            file = self.confirmation(plan, {})
            self.assertEqual(self.invoke(state, '--force-confirm', '--confirm-file', file)[0], 2)
            self.assertEqual(len(state.posts), 1)
            file = self.confirmation(plan, {'project': {'action': 'reuse', 'id': 'project_v3'}})
            self.assertEqual(self.invoke(state, '--force-confirm', '--confirm-file', file)[0], 0)
            self.assertEqual(state.posts[-1]['body']['project'], {'id': 'project_v3'})

    def test_resume_422_preserves_choices_from_pending(self):
        with server() as state:
            self.add_project(state, 'v2')
            _, text = self.invoke(state, '--force-confirm')
            file = self.confirmation(json.loads(text), {'project': {'action': 'reuse', 'id': 'project_v2'}})
            state.responses = [(500, {}, {}), (500, {}, {})]
            self.assertEqual(self.invoke(state, '--force-confirm', '--confirm-file', file)[0], 4)
            meta = json.loads(next((self.home / 'docs/.pending').glob('*.meta.json')).read_text())
            state.responses = [(422, dict(reason='taxonomy_confirmation_required',
                                          details=dict(items=[dict(field='model', candidates=[])])), {})]
            code, text = self.invoke(state, '--resume', meta['client_upload_id'])
            self.assertEqual(code, 3)
            file = self.confirmation(json.loads(text), {'model': {'action': 'reuse', 'id': 'model_example'}})
            self.assertEqual(self.invoke(state, '--force-confirm', '--confirm-file', file)[0], 0)
            self.assertEqual(state.posts[-1]['body']['project'], {'id': 'project_v2'})

    def test_external_control_characters_never_reach_text_report(self):
        self.html.write_bytes(page('<a href="https://bad.invalid/a\x1b[31m">bad</a>'
                                   '<img src="https://bad.invalid/a\u202eb">'
                                   '<a href="https://safe.invalid/a?x=%0A">safe</a>'))
        with server() as state:
            code, text = self.invoke(state)
            self.assertEqual(code, 0)
            report = json.loads(text)['report']
            self.assertNotIn('\x1b', report)
            self.assertNotIn('\u202e', report)
            self.assertNotIn('bad.invalid', report)
            self.assertIn('https://safe.invalid/a?x=%0A', report)

    def test_deep_confirmation_json_returns_exit_2(self):
        file = self.home / 'deep-confirmation.json'
        file.write_text('[' * 10000 + '0' + ']' * 10000)
        with server() as state:
            try:
                code, _ = self.invoke(state, '--confirm-file', str(file))
            except RecursionError:
                self.fail('confirmation JSON recursion escaped the upload entrypoint')
            self.assertEqual(code, 2)
            self.assertEqual(state.posts, [])

    def test_deep_saved_plan_json_returns_exit_2(self):
        with server() as state:
            _, text = self.invoke(state, '--force-confirm')
            plan = json.loads(text)
            file = self.confirmation(plan, {})
            saved = self.home / 'docs/.confirm' / (plan['client_upload_id'] + '.plan.json')
            saved.write_text('[' * 10000 + '0' + ']' * 10000)
            try:
                code, _ = self.invoke(state, '--force-confirm', '--confirm-file', file)
            except RecursionError:
                self.fail('saved plan JSON recursion escaped the upload entrypoint')
            self.assertEqual(code, 2)
            self.assertEqual(state.posts, [])

    def test_deep_pending_meta_and_request_return_exit_4(self):
        with server() as state:
            state.responses = [(422, dict(reason='html_rejected'), {})]
            self.assertEqual(self.invoke(state)[0], 2)
            path = next((self.home / 'docs/.pending').glob('*.meta.json'))
            client_id = json.loads(path.read_text())['client_upload_id']
            request_path = path.with_name(client_id + '.request.json')
            for target in (path, request_path):
                with self.subTest(file=target.suffixes):
                    original = target.read_bytes()
                    try:
                        target.write_text('[' * 10000 + '0' + ']' * 10000)
                        try:
                            code, text = self.invoke(state, '--resume', client_id)
                        except RecursionError:
                            self.fail('pending JSON recursion escaped the upload entrypoint')
                        self.assertEqual(code, 4)
                        self.assertIn('remote_unknown', text)
                        self.assertEqual(len(state.posts), 1)
                    finally:
                        target.write_bytes(original)

    def test_resume_and_dry_run_are_rejected_before_credentials(self):
        with server() as state:
            code, text = self.invoke(state, '--resume', 'a' * 32, '--dry-run')
            self.assertEqual(code, 2)
            self.assertIn('--resume', text)
            self.assertIn('--dry-run', text)
            self.assertEqual(state.gets, [])
            self.assertEqual(state.posts, [])
            self.assertFalse((self.home / 'docs').exists())

    def test_invalid_success_title_keeps_pending_and_no_success_event(self):
        with server() as state:
            result = success_result('Unsafe\u202etitle', state.origin,
                                    hashlib.sha256(self.html.read_bytes()).hexdigest())
            state.responses = [(201, result, {})]
            code, text = self.invoke(state)
            self.assertEqual(code, 4)
            self.assertIn('remote_unknown', text)
            self.assertEqual(len(state.posts), 1)
            self.assertFalse((self.home / 'docs' / ('a' * 32 + '.html')).exists())
            self.assertEqual(len(list((self.home / 'docs/.pending').glob('*.html'))), 1)
            events = [json.loads(line) for line in (self.home / 'docs/manifest.jsonl').read_text().splitlines()]
            self.assertEqual([event['status'] for event in events], ['prepared', 'remote_unknown'])


def success_result(title, origin='http://127.0.0.1:18080', sha='b' * 64):
    return dict(id='a' * 32, url=origin + '/' + 'a' * 32 + '.html', sha256=sha,
                uploaded_at='2026-10-20T00:00:00Z', acl_version=1,
                normalized_metadata=dict(title=title, generated_at='2026-10-15T04:00:00Z',
                                         metadata_source={'time': 'html_meta', 'model': 'html_meta'},
                                         project={'id': 'project_docs', 'name': 'Docs'},
                                         type={'id': 'type_design', 'name': 'Design'},
                                         model={'id': 'model_example', 'name': 'ExampleModel'}))


class P6Validation(unittest.TestCase):
    def test_server_candidates_reject_invalid_values(self):
        good = {'id': 'type_good', 'name': 'Good', 'score': 0.95}
        invalid = [
            {'id': 'x' * 65, 'name': 'Bad'}, {'id': '../bad', 'name': 'Bad'},
            {'id': 123, 'name': 'Bad'}, {'id': 'type_bad', 'name': 'x' * 81},
            {'id': 'type_bad', 'name': 'x' * 10000}, {'id': 'type_bad', 'name': '\u00df' * 41},
            {'id': 'type_bad', 'name': 'Bad\x1bname'}, {'id': 'type_bad', 'name': 'Bad\u202ename'},
            {'id': 'type_bad', 'name': None}, {'id': 'type_bad', 'name': ''},
            {'id': 'type_bad', 'name': 'Bad', 'score': '9' * 10000},
            {'id': 'type_bad', 'name': 'Bad', 'score': {'nested': 'untrusted'}},
            {'id': 'type_bad', 'name': 'Bad', 'score': True},
            {'id': 'type_bad', 'name': 'Bad', 'score': -0.1},
            {'id': 'type_bad', 'name': 'Bad', 'score': 1.1},
            {'id': 'type_bad', 'name': 'Bad', 'score': float('nan')},
            {'id': 'type_bad', 'name': 'Bad', 'score': float('inf')},
        ]
        for index, candidate in enumerate(invalid):
            with self.subTest(candidate=index):
                plan = {'items': [{'field': 'type', 'status': 'matched'}]}
                try:
                    upload.merge_server_items(plan, {'items': [{'field': 'type', 'candidates': [good, candidate]}]})
                except (TypeError, ValueError):
                    self.fail('invalid candidate escaped sanitization')
                self.assertEqual(plan['items'][0]['candidates'], [good])
                self.assertEqual(plan['server_details']['items'][0]['candidates'], [good])

    def test_server_candidates_normalize_display_names_and_bound_count(self):
        candidates = [{'id': 't' + str(i), 'name': '  Ｄｅｓｉｇｎ ', 'score': 1} for i in range(7)]
        plan = {'items': [{'field': 'type', 'status': 'matched'}]}
        upload.merge_server_items(plan, {'items': [{'field': 'type', 'candidates': candidates}]})
        self.assertEqual(plan['items'][0]['candidates'],
                         [{'id': 't' + str(i), 'name': 'Design', 'score': 1} for i in range(5)])

    def test_server_candidates_ignore_non_list_collections(self):
        for candidates in (None, {}, 'untrusted'):
            with self.subTest(candidates=candidates):
                plan = {'items': [{'field': 'type', 'status': 'matched'}]}
                try:
                    upload.merge_server_items(plan, {'items': [{'field': 'type', 'candidates': candidates}]})
                except (TypeError, ValueError, KeyError):
                    self.fail('malformed candidates escaped sanitization')
                self.assertEqual(plan['items'][0]['candidates'], [])

    def test_success_title_uses_title_contract(self):
        for title in (None, '', ' ', ' leading', 'trailing ', 'x' * 201, 'Bad\x1btitle', 'Bad\u202etitle'):
            with self.subTest(title=repr(title)):
                self.assertFalse(upload.valid_success(success_result(title), 'http://127.0.0.1:18080', 'b' * 64, None))
        for title in ('Title', '长标题', 'x' * 200):
            with self.subTest(length=len(title)):
                self.assertTrue(upload.valid_success(success_result(title), 'http://127.0.0.1:18080', 'b' * 64, None))

