"""Run inside web to verify the deployed NGINX proxy's HTTP boundaries."""
from http.client import HTTPConnection
import socket
import time
from urllib.request import urlopen

with urlopen('http://nginx:8080/login', timeout=5) as response:
    assert response.status == 200
    assert response.headers['Server'] == 'nginx'
    assert response.headers['Content-Security-Policy']
    assert response.headers['Cache-Control'] == 'no-store'
connection = HTTPConnection('nginx', 8080, timeout=5)
connection.request('POST', '/login', body=b'x' * 8193)
response = connection.getresponse()
assert response.status == 413
response.read()
connection.close()
# Incomplete headers must be rejected without keeping a backend thread occupied.
with socket.create_connection(('nginx', 8080), timeout=15) as connection:
    connection.sendall(b'GET /login HTTP/1.1\r\nHost: localhost\r\n')
    started = time.monotonic()
    data = connection.recv(4096)
    # NGINX may close an incomplete header silently rather than send a 408.
    assert not data or b'408' in data.split(b'\r\n', 1)[0], data
    assert 8 <= time.monotonic() - started < 15
print('PASS: proxy serves login, preserves security headers, rejects oversized bodies, and expires incomplete headers.')
