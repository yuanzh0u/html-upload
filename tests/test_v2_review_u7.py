"""U7: bound actual HTTP body sends without changing wire bytes or retries."""
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
import client


class RecordingSocket:
    """Replace only network I/O; urllib and http.client still frame requests."""
    def __init__(self, status=201, fail_body_call=None):
        self.status = status
        self.fail_body_call = fail_body_call
        self.timeout = None
        self.sends = []
        self.send_timeouts = []
        self.read_timeouts = []

    def setsockopt(self, *args):
        pass

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendall(self, data):
        self.sends.append(bytes(data))
        self.send_timeouts.append(self.timeout)
        if len(self.sends) - 1 == self.fail_body_call:
            raise OSError('simulated partial body send')

    def makefile(self, mode):
        owner = self

        class ResponseBytes(io.BytesIO):
            def read(self, *args):
                owner.read_timeouts.append(owner.timeout)
                return super().read(*args)

            def readline(self, *args):
                owner.read_timeouts.append(owner.timeout)
                return super().readline(*args)

        value = b'{"ok":true}'
        return ResponseBytes(f'HTTP/1.1 {self.status} response\r\nContent-Type: application/json\r\n'
                             f'Content-Length: {len(value)}\r\nConnection: close\r\n\r\n'.encode() + value)

    def close(self):
        pass


class ChunkedBodyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.token = 'review-u7-fake-key-0000000000'
        env = patch.dict(os.environ, {'HOME': temporary.name, 'DOCS_API_KEY': self.token,
                                     'DOCS_ORIGIN': 'http://127.0.0.1:9'})
        env.start()
        self.addCleanup(env.stop)

    def network(self, sockets):
        pending = iter(sockets)
        connections = []

        def connect(address, timeout, source_address=None):
            self.assertEqual(address[0], '127.0.0.1')
            self.assertEqual(timeout, 10)
            connections.append(address)
            return next(pending)

        class TLSContext:
            def wrap_socket(self, sock, *, server_hostname):
                self.assertEqual(server_hostname, '127.0.0.1')
                return sock

        context = TLSContext()
        # The context only substitutes TLS I/O, leaving HTTPSConnection.connect real.
        context.assertEqual = self.assertEqual
        connection_patch = patch('socket.create_connection', side_effect=connect)
        tls_patch = patch.object(client.ssl, 'create_default_context', return_value=context)
        connection_patch.start()
        self.addCleanup(connection_patch.stop)
        tls_patch.start()
        self.addCleanup(tls_patch.stop)
        return connections

    def assert_wire(self, sock, raw, idem=None):
        header_block = sock.sends[0]
        self.assertTrue(header_block.endswith(b'\r\n\r\n'))
        lines = header_block.decode('iso-8859-1').split('\r\n')
        headers = dict(line.split(': ', 1) for line in lines[1:] if line)
        self.assertEqual(headers.get('Content-Length'), str(len(raw)))
        self.assertNotIn('Transfer-Encoding', headers)
        self.assertEqual(headers.get('Authorization'), 'Bearer ' + self.token)
        self.assertEqual(headers.get('Idempotency-Key'), idem)
        body_sends = sock.sends[1:]
        self.assertEqual(b''.join(body_sends), raw)
        self.assertTrue(all(0 < len(chunk) <= 65536 for chunk in body_sends),
                        f'body send sizes: {[len(chunk) for chunk in body_sends]}')
        self.assertEqual(sock.send_timeouts, [30] * len(sock.sends))
        self.assertTrue(sock.read_timeouts)
        self.assertEqual(set(sock.read_timeouts), {30})

    def test_http_and_https_send_at_most_64kib_with_exact_length_and_raw_bytes(self):
        for scheme in ('http', 'https'):
            # The largest body exceeds 2 MiB and splits inside UTF-8 characters.
            for raw in (b'x' * 65535, b'x' * 65536, b'x' * 65537,
                        b'{"html":"' + ('页' * 700000).encode() + b'"}'):
                with self.subTest(scheme=scheme, size=len(raw)):
                    sock = RecordingSocket()
                    self.network([sock])
                    result = client.http_request('POST', scheme + '://127.0.0.1:9/api/v2/documents',
                                                 self.token, raw, 'u7-idempotency')
                    self.assertEqual(result[:2], (201, {'ok': True}))
                    self.assert_wire(sock, raw, 'u7-idempotency')

    def test_partial_body_network_failure_retries_from_first_byte_with_same_key(self):
        raw = b'{"html":"' + ('正文' * 40000).encode() + b'"}'
        first, second = RecordingSocket(fail_body_call=2), RecordingSocket()
        connections = self.network([first, second])
        api = client.Client('http://127.0.0.1:9', self.token, sleep=lambda _: None,
                            rng=lambda a, b: 0)
        result = api.send_idempotent('PUT', '/api/v2/documents/doc/content', raw, 'same-key')
        self.assertEqual(result[:2], (201, {'ok': True}))
        self.assertEqual(len(connections), 2)
        self.assertEqual(b''.join(first.sends[1:]), raw[:131072])
        self.assertIn(b'Idempotency-Key: same-key\r\n', first.sends[0])
        self.assert_wire(second, raw, 'same-key')

    def test_transient_response_retries_complete_body_with_same_length_and_key(self):
        raw = b'{"html":"' + ('重试' * 40000).encode() + b'"}'
        first, second = RecordingSocket(status=503), RecordingSocket(status=200)
        connections = self.network([first, second])
        api = client.Client('https://127.0.0.1:9', self.token, sleep=lambda _: None,
                            rng=lambda a, b: 0)
        result = api.send_idempotent('POST', '/api/v2/documents', raw, 'same-key')
        self.assertEqual(result[:2], (200, {'ok': True}))
        self.assertEqual(len(connections), 2)
        self.assert_wire(first, raw, 'same-key')
        self.assert_wire(second, raw, 'same-key')

    def test_empty_body_has_zero_length_and_get_has_no_body_framing(self):
        empty, get = RecordingSocket(), RecordingSocket(status=200)
        self.network([empty, get])
        self.assertEqual(client.http_request('PATCH', 'http://127.0.0.1:9/doc', self.token, b'')[0], 201)
        self.assert_wire(empty, b'')
        self.assertEqual(client.http_request('GET', 'http://127.0.0.1:9/doc', self.token)[0], 200)
        self.assertEqual(len(get.sends), 1)
        self.assertNotIn(b'Content-Length:', get.sends[0])
        self.assertNotIn(b'Transfer-Encoding:', get.sends[0])


if __name__ == '__main__':
    unittest.main()
