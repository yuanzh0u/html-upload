#!/usr/bin/env python3
"""Independent P6 spot-check harness. Read-only w.r.t. delivered files."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path('/home/ryan/projects/html-platform/html-upload')
sys.path.insert(0, str(ROOT / 'bin'))
sys.path.insert(0, str(ROOT / 'tests'))
import upload
import check_html
from fake_server import server

KEY = 'test-key-not-secret'
HTML = (b'<!doctype html><html><head><title>E2E Title</title>'
        b'<meta name="plans:completed" content="2026-10-15T12:00:00+08:00">'
        b'<meta name="plans:worker" content="ExampleModel"></head><body>ok</body></html>')
NO_META = (b'<!doctype html><html><head><title>No Meta Title</title></head><body>ok</body></html>')

results = []

def record(name, ok, detail):
    results.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + ' :: ' + detail)

def invoke(home, state, html, extra=(), env_key=KEY):
    out = io.StringIO()
    env = dict(HOME=str(home), DOCS_ORIGIN=state.origin)
    if env_key is not None:
        env['DOCS_API_KEY'] = env_key
    err = io.StringIO()
    code = None
    with contextlib.redirect_stderr(err):
        code = upload.run([str(html), '--project-name', 'Docs', '--type-name', 'Design',
                           '--non-interactive'] + list(extra),
                          env=env, out=out, transport=state.transport, sleep=lambda _: None)
    text = out.getvalue()
    if KEY in text or KEY in err.getvalue():
        raise AssertionError('credential leaked into output')
    return code, text

# 1. CRLF file credential accepted.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    keydir = home / '.config/docs'
    keydir.mkdir(parents=True, mode=0o700)
    keyfile = keydir / 'api-key'
    keyfile.write_bytes(b'test-key-not-secret\r\n')
    keyfile.chmod(0o600)
    html = home / 'a.html'
    html.write_bytes(HTML)
    before = html.read_bytes()
    with server() as state:
        code, text = invoke(home, state, html, env_key=None)
        ok = code == 0 and len(state.posts) == 1 and state.posts[0]['auth_ok'] and html.read_bytes() == before
        record('CRLF-file-credential', ok,
               f'exit={code} posts={len(state.posts)} auth_ok={state.posts[0]["auth_ok"] if state.posts else None} src_bytes_unchanged={html.read_bytes()==before}')
        record('CRLF-source-not-leaked', 'test-key-not-secret\\r\\n' not in text and KEY not in text, 'stdout/stderr free of key bytes')

# 2. charset=gbk rejected, exit 2, zero GET/POST.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    html = home / 'gbk.html'
    html.write_bytes(HTML.replace(b'<title>', b'<meta charset="gbk"><title>'))
    raised = False
    try:
        check_html.inspect_html(html.read_bytes())
    except ValueError as e:
        raised = True
    with server() as state:
        code, text = invoke(home, state, html)
        record('charset-gbk-direct', raised, 'inspect_html raised ValueError')
        record('charset-gbk-upload', code == 2 and state.gets == [] and state.posts == [] and 'upload_failed' in text,
               f'exit={code} gets={len(state.gets)} posts={len(state.posts)}')

# 3. Consecutive 422 preserves non-invalidated confirmed choices.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    html = home / 'a.html'
    html.write_bytes(HTML)
    with server() as state:
        state.tax['projects'].append(dict(id='project_v2', name='Docs V2', category_id='cat_software', selectable_for_upload=True))
        # force-confirm pauses; choose project_v2 explicitly.
        code1, text1 = invoke(home, state, html, ['--force-confirm'])
        plan1 = json.loads(text1)
        confdir = home / 'docs/.confirm'
        conf1 = home / 'conf1.json'
        conf1.write_text(json.dumps(dict(client_upload_id=plan1['client_upload_id'],
                                         plan_sha256=plan1['plan_sha256'], accepted=True,
                                         choices={'project': {'action': 'reuse', 'id': 'project_v2'}})))
        # First POST is answered with 422 for model.
        state.responses = [(422, dict(reason='taxonomy_confirmation_required',
                                      details=dict(items=[dict(field='model', candidates=[dict(id='model_example', name='ExampleModel')])])), {})]
        code2, text2 = invoke(home, state, html, ['--force-confirm', '--confirm-file', str(conf1)])
        plan2 = json.loads(text2)
        retained = plan2.get('confirmed_choices', {}).get('project', {}).get('id')
        # Confirm only model; project must survive into the successful request.
        conf2 = home / 'conf2.json'
        conf2.write_text(json.dumps(dict(client_upload_id=plan2['client_upload_id'],
                                         plan_sha256=plan2['plan_sha256'], accepted=True,
                                         choices={'model': {'action': 'reuse', 'id': 'model_example'}})))
        code3, text3 = invoke(home, state, html, ['--force-confirm', '--confirm-file', str(conf2)])
        body = state.posts[-1]['body'] if state.posts else {}
        ok = (code2 == 3 and retained == 'project_v2' and code3 == 0 and body.get('project') == {'id': 'project_v2'})
        record('consecutive-422-preserves-choices', ok,
               f'exit_round2={code2} retained_project={retained} exit_round3={code3} final_project={body.get("project")} posts={len(state.posts)}')

# 4. ESC URL never reaches plain-text report; safe URL survives.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    html = home / 'links.html'
    html.write_bytes(HTML.replace(b'</head>',
        b'<a href="https://bad.invalid/a\x1b[31m">bad</a>'
        b'<a href="https://safe.invalid/a?x=1">safe</a></head>'))
    ext = check_html.inspect_html(html.read_bytes())['external_links']
    with server() as state:
        code, text = invoke(home, state, html)
        value = json.loads(text)
        report = value['report']
        ok = (code == 0 and 'bad.invalid' not in ext['urls'] and 'https://safe.invalid/a?x=1' in ext['urls']
              and '\x1b' not in report and 'bad.invalid' not in report and 'https://safe.invalid/a?x=1' in report)
        record('esc-url-not-in-report', ok,
               f'exit={code} inspected_urls={ext["urls"]} report_has_esc={chr(27) in report} report_has_bad={"bad.invalid" in report} report_has_safe={"https://safe.invalid/a?x=1" in report}')

# 5a. 100k-deep confirm JSON -> exit 2, zero POST.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    html = home / 'a.html'
    html.write_bytes(HTML)
    deep = home / 'deep.json'
    deep.write_text('[' * 100000 + '0' + ']' * 100000)
    with server() as state:
        code, text = invoke(home, state, html, ['--confirm-file', str(deep)])
        record('deep-100k-confirm-exit2', code == 2 and state.posts == [],
               f'exit={code} posts={len(state.posts)} status_line={text.splitlines()[0] if text else ""}')

# 5b. 100k-deep pending meta on --resume -> exit 4 (remote_unknown), zero POST.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    pending = home / 'docs/.pending'
    pending.mkdir(parents=True, mode=0o700)
    os.chmod(home / 'docs', 0o700)
    cid = 'a' * 32
    (pending / (cid + '.meta.json')).write_text('[' * 100000 + '0' + ']' * 100000)
    html = home / 'shape.html'
    html.write_bytes(HTML)
    with server() as state:
        out = io.StringIO()
        env = dict(HOME=str(home), DOCS_API_KEY=KEY, DOCS_ORIGIN=state.origin)
        code = upload.run([str(html), '--resume', cid, '--non-interactive'],
                          env=env, out=out, transport=state.transport, sleep=lambda _: None)
        text = out.getvalue()
        record('deep-100k-pending-exit4', code == 4 and state.posts == [] and 'remote_unknown' in text,
               f'exit={code} posts={len(state.posts)} has_remote_unknown={"remote_unknown" in text}')

# 6. --resume --dry-run -> exit 2 before any network / local state.
with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    html = home / 'a.html'
    html.write_bytes(HTML)
    with server() as state:
        code, text = invoke(home, state, html, ['--resume', 'a' * 32, '--dry-run'])
        record('resume-dry-run-exit2', code == 2 and state.gets == [] and state.posts == [] and not (home / 'docs').exists(),
               f'exit={code} gets={len(state.gets)} posts={len(state.posts)} docs_dir={(home/"docs").exists()}')

print('\nSUMMARY ' + json.dumps({'total': len(results), 'failed': [r[0] for r in results if not r[1]]}))
sys.exit(0 if all(r[1] for r in results) else 1)
