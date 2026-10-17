"""Exercise backend socket deadlines, concurrency limits, and proxy-header trust."""
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace
from urllib.request import urlopen
from app import BoundedHTTPServer, Handler

class SmallServer(BoundedHTTPServer):
    max_requests = 2
    request_timeout = 0.5

class Page(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *args):
        pass

server = SmallServer(('127.0.0.1', 0), Page)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
connections = []
try:
    address = server.server_address
    for _ in range(2):
        connection = socket.create_connection(address, timeout=2)
        connection.sendall(b'GET /')
        connections.append(connection)
    deadline = time.monotonic() + 2
    while server.slots._value and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.slots._value == 0
    with socket.create_connection(address, timeout=2) as excess:
        assert excess.recv(1) == b''
    # Both incomplete request headers must expire, freeing the slots.
    for connection in connections:
        assert connection.recv(1) == b''
    with urlopen(f'http://127.0.0.1:{address[1]}/', timeout=2) as response:
        assert response.read() == b'ok'
finally:
    for connection in connections:
        connection.close()
    server.shutdown()
    server.server_close()
    worker.join()

os.environ['TRUSTED_PROXY_IP'] = '172.30.82.20'
request = SimpleNamespace(client_address=('172.30.82.10', 1234), headers={'X-Real-IP': '192.0.2.10'})
assert Handler.client_ip(request) == '172.30.82.10'
request.client_address = ('172.30.82.20', 1234)
assert Handler.client_ip(request) == '192.0.2.10'
request.headers['X-Real-IP'] = 'invalid'
assert Handler.client_ip(request) == '172.30.82.20'
print('PASS: HTTP threads are bounded, incomplete requests expire, and proxy headers require a trusted peer.')
