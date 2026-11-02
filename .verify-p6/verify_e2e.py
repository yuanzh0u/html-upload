#!/usr/bin/env python3
"""Independent end-to-end dry-run / pause / success walk-through with temp HOME + local stub."""
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
from fake_server import server

KEY = 'test-key-not-secret'
GOOD = (b'<!doctype html><html><head><title>P6 E2E Title</title>'
        b'<meta name="plans:completed" content="2026-10-15T12:00:00+08:00">'
        b'<meta name="plans:worker" content="ExampleModel"></head>'
        b'<body><p>compliance fixture</p></body></html>')
MISSING = (b'<!doctype html><html><head><title>P6 Missing Meta</title></head>'
           b'<body><p>no plans metadata</p></body></html>')
results = []

def record(name, ok, detail):
    results.append((name, ok, detail))
    print(('PASS ' if ok else 'FAIL ') + name + ' :: ' + detail)

def invoke(home, state, html, extra=()):
    out = io.StringIO()
    err = io.StringIO()
    env = dict(HOME=str(home), DOCS_API_KEY=KEY, DOCS_ORIGIN=state.origin)
    with contextlib.redirect_stderr(err):
        code = upload.run([str(html), '--project-name', 'Docs', '--type-name', 'Design',
                           '--non-interactive'] + list(extra),
                          env=env, out=out, transport=state.transport, sleep=lambda _: None)
    text = out.getvalue()
    assert KEY not in text + err.getvalue(), 'key leaked'
    return code, text

with tempfile.TemporaryDirectory() as td:
    home = Path(td)
    good = home / 'compliant.html'
    missing = home / 'missing-meta.html'
    good.write_bytes(GOOD)
    missing.write_bytes(MISSING)
    good_before = (good.read_bytes(), good.stat().st_mtime_ns)
    missing_before = missing.read_bytes()

    with server() as state:
        # (a) compliant dry-run: zero POST, no local state, explicit dry-run sentence.
        code, text = invoke(home, state, good, ['--dry-run'])
        dry = json.loads(text)
        record('e2e-dry-run-compliant', code == 0 and len(state.posts) == 0 and not (home / 'docs').exists()
               and '未提交，没有 docid' in text and dry['status'] == 'dry_run',
               f'exit={code} posts={len(state.posts)} gets={len(state.gets)} docs_dir={(home/"docs").exists()} status={dry.get("status")}')
        record('e2e-dry-run-no-pending-manifest',
               not (home / 'docs/.pending').exists() and not (home / 'docs/manifest.jsonl').exists(),
               'no .pending and no manifest after dry-run')
        record('e2e-dry-run-notes',
               '服务端未返回 Key 有效期' in text and '服务端未返回上传限制' in text,
               'both missing-server-field notes present')

        # (b) missing meta: exit 3, no POST, no pending, plan JSON delivered.
        code, text = invoke(home, state, missing)
        plan = json.loads(text)
        fields = {x.get('field') for x in plan.get('items', [])}
        record('e2e-missing-meta-pause', code == 3 and len(state.posts) == 0 and plan['status'] == 'needs_confirmation'
               and not (home / 'docs/.pending').exists(),
               f'exit={code} posts={len(state.posts)} status={plan.get("status")} issue_fields={sorted(f for f in fields if f)} pending={(home/"docs/.pending").exists()}')
        record('e2e-missing-meta-no-submit', state.posts == [] and missing.read_bytes() == missing_before,
               f'posts={len(state.posts)} source_unchanged={missing.read_bytes()==missing_before}')

        # (c) success path: one POST, backup/manifest/report complete, bytes identical.
        code, text = invoke(home, state, good)
        value = json.loads(text)
        import re
        report_text = value.get('report', '')
        match = re.search(r'^docid: ([0-9a-f]{32})$', report_text, re.M)
        docid = match.group(1) if match else None
        backup = home / 'docs' / (docid + '.html') if docid else None
        expected_sha = hashlib.sha256(GOOD).hexdigest()
        manifest = home / 'docs/manifest.jsonl'
        events = [json.loads(line) for line in manifest.read_text().splitlines()] if manifest.exists() else []
        headings = ['状态:', '线上链接:', 'docid:', '本地副本:', 'manifest:', '外链提示：', '待办:']
        ok = (code == 0 and len(state.posts) == 1 and docid and backup and backup.exists()
              and backup.read_bytes() == GOOD and hashlib.sha256(backup.read_bytes()).hexdigest() == expected_sha
              and events and events[-1]['status'] == 'uploaded' and events[-1]['docid'] == docid
              and all(h in report_text for h in headings) and '未发现 http(s)' in report_text)
        record('e2e-success-trio', ok,
               f'exit={code} posts={len(state.posts)} docid={docid} backup={backup} bytes_equal={backup is not None and backup.exists() and backup.read_bytes()==GOOD} manifest_last={events[-1]["status"] if events else None} report_headings={all(h in report_text for h in headings)}')
        record('e2e-success-source-untouched', (good.read_bytes(), good.stat().st_mtime_ns) == good_before,
               'source bytes and mtime unchanged')
        record('e2e-success-no-pending-html', not list((home / 'docs/.pending').glob('*.html')),
               f'pending_html={[p.name for p in (home/"docs/.pending").glob("*.html")]}')

print('\nSUMMARY ' + json.dumps({'total': len(results), 'failed': [r[0] for r in results if not r[1]]}))
sys.exit(0 if all(r[1] for r in results) else 1)
