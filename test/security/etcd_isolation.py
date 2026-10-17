"""Run inside web: neither etcd client nor peer listener may be reachable."""
import socket
for node in (1, 2, 3):
    for host in (f'etcd{node}', f'etcd{node}-dcs', f'172.30.81.{10+node}'):
        for port in (2379, 2380):
            try:
                connection = socket.create_connection((host, port), timeout=1)
            except OSError:
                pass
            else:
                connection.close()
                raise AssertionError(f'Coordination service reachable from web: {host}:{port}')
print('PASS: web cannot reach any etcd client or peer listener.')
