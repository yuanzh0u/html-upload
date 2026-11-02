# Force the fake_server fixture down its documented in-process transport fallback,
# reproducing the implementation environment where loopback sockets are denied.
import socket
_original_bind = socket.socket.bind
def _denied_bind(self, address):
    raise PermissionError(13, 'sandbox denies socket bind')
socket.socket.bind = _denied_bind
