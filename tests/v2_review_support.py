"""Loopback HTTP fixture for supplemental review regressions only."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import threading

from v2_fake import FakeDocs


class LoopbackDocs:
    """Serve the existing contract fake through the real HTTP client."""
    def __init__(self, test):
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self):
                size = int(self.headers.get('Content-Length', '0'))
                body = self.rfile.read(size) if size else None
                status, data, headers = fixture.fake.transport(
                    self.command, fixture.origin + self.path,
                    self.headers.get('Authorization', '').removeprefix('Bearer '),
                    body, self.headers.get('Idempotency-Key'))
                raw = json.dumps(data, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PUT = respond

        server = HTTPServer(('127.0.0.1', 0), Handler)
        test.addCleanup(server.server_close)
        self.origin = f'http://127.0.0.1:{server.server_port}'
        self.fake = FakeDocs(origin=self.origin)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        test.addCleanup(thread.join)
        test.addCleanup(server.shutdown)
