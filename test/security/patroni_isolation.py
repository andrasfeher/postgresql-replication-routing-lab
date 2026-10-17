"""Run inside web: management APIs must not be reachable from pgnet."""
import socket
from urllib.request import urlopen

for node in (1, 2, 3):
    for host in (f'pg-node{node}', f'172.30.80.{10+node}'):
        try:
            connection = socket.create_connection((host, 8008), timeout=2)
        except OSError:
            pass
        else:
            connection.close()
            raise AssertionError('Management API reachable from web: ' + host)
with urlopen('http://127.0.0.1:8080/', timeout=10) as response:
    assert response.status == 200
print('PASS: web cannot connect to any management API; application still responds.')
