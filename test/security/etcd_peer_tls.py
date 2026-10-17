"""Run in a disposable container sharing etcd1 networking with individual cert mounts."""
import ssl
from urllib.error import URLError
from urllib.request import urlopen

trusted = ssl.create_default_context(cafile='/tls/peer-ca.crt')
trusted.load_cert_chain('/tls/peer.crt', '/tls/peer.key')
wrong = ssl.create_default_context(cafile='/tls/peer-ca.crt')
wrong.load_cert_chain('/tls/client.crt', '/tls/client.key')
missing = ssl.create_default_context(cafile='/tls/peer-ca.crt')
for node in (1, 2, 3):
    url = f'https://etcd{node}-dcs:2380/version'
    with urlopen(url, context=trusted, timeout=5) as response:
        assert response.status == 200
    for context in (wrong, missing):
        try:
            urlopen(url, context=context, timeout=5)
        except (URLError, OSError):
            pass
        else:
            raise AssertionError('Peer listener accepted an unauthorized client certificate')
print('PASS: peer mTLS accepts peer certificates and rejects missing or client-CA certificates.')
