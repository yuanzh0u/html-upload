"""Loopback-only HTTP fixture. Never logs credentials or connects outward."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading


def taxonomy():
    return dict(version=12, project_categories=[dict(id='cat_software', name='Software', selectable_for_upload=True)], projects=[dict(id='project_docs', name='Docs', category_id='cat_software', selectable_for_upload=True, aliases=['docs-service'])], types=[dict(id='type_design', name='Design', aliases=['draft'], selectable_for_upload=True)], models=[dict(id='model_example', name='ExampleModel', selectable_for_upload=True)])


class State:
    def __init__(self):
        self.gets = []
        self.posts = []
        self.me_status = 200
        self.me = dict(user_id='user_demo', display_name='Demo', role='user', kind='api_key', scopes=['me:read','taxonomy:read','documents:create'])
        self.tax = taxonomy()
        self.responses = []
        self.redirect = None
        self.origin = None
        self.docid = 'a' * 32
        self.sleep_events = []
        self.ids = {}
        self.bad_success = False
        self.transport = None
        self.loopback_unavailable = False

    def success(self, body, replayed=False):
        idem = self.posts[-1]['headers']['Idempotency-Key']
        if idem not in self.ids: self.ids[idem] = format(int('a'*32,16)+len(self.ids),'032x')
        self.docid = self.ids[idem]
        return dict(id=self.docid, url=f'{self.origin}/{self.docid}.html', sha256=body['sha256'], uploaded_at='2026-10-20T00:00:00.000000Z', acl_version=1, normalized_metadata={**{k: dict(id=f'{k}_server', name=f'Server {k}') for k in ('project','type','model')}, 'generated_at':body['generated_at'], 'metadata_source':body['metadata_source'], 'title':body['title']}, replayed=replayed)


@contextmanager
def server():
    state = State()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, code, body, headers=None):
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(encoded)))
            for k,v in (headers or {}).items(): self.send_header(k,v)
            self.end_headers()
            self.wfile.write(encoded)
        def do_GET(self):
            state.gets.append(self.path)
            if state.redirect:
                return self.send(302, {}, {'Location':state.redirect})
            if self.path == '/api/v1/me': return self.send(state.me_status, state.me)
            if self.path == '/api/v1/taxonomy?include=aliases,counts': return self.send(200, state.tax)
            self.send(404, {})
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            body = json.loads(raw)
            state.posts.append(dict(raw=raw, body=body, headers={k:v for k,v in self.headers.items() if k.lower() != 'authorization'}, auth_ok=self.headers.get('Authorization') == 'Bearer test-key-not-secret', sleeps=list(state.sleep_events)))
            if state.responses:
                status, result, headers = state.responses.pop(0)
                if result == 'success': result = state.success(body, replayed=status==200)
                return self.send(status, result, headers)
            result = state.success(body)
            if state.bad_success: result.pop('id')
            self.send(201, result)
    try:
        httpd = ThreadingHTTPServer(('127.0.0.1',0), Handler)
    except PermissionError:
        httpd = None
    if httpd is None:
        # Sandbox denies socket creation. Keep this explicit in validation reports.
        state.loopback_unavailable = True
        state.origin = 'http://127.0.0.1:18080'
        def transport(method, url, token, body=None, idem=None):
            assert url.startswith(state.origin + '/')
            path = url[len(state.origin):]
            if method == 'GET':
                state.gets.append(path)
                if state.redirect: return 302, {}, {'Location':state.redirect}
                if path == '/api/v1/me': return state.me_status, state.me, {}
                if path == '/api/v1/taxonomy?include=aliases,counts': return 200, state.tax, {}
                return 404, {}, {}
            assert path == '/api/v1/documents'
            value = json.loads(body)
            state.posts.append(dict(raw=body, body=value, headers={'Content-Type':'application/json','Origin':state.origin,'Idempotency-Key':idem,'Content-Length':str(len(body))}, auth_ok=token=='test-key-not-secret', sleeps=list(state.sleep_events)))
            if state.responses:
                status, result, headers = state.responses.pop(0)
                if result == 'success': result = state.success(value, replayed=status==200)
                return status, result, headers
            result = state.success(value)
            if state.bad_success: result.pop('id')
            return 201, result, {}
        state.transport = transport
        yield state
        return
    httpd.daemon_threads = True
    state.origin = f'http://127.0.0.1:{httpd.server_port}'
    thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval':0.01}, daemon=True)
    thread.start()
    try: yield state
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
