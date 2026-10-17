"""Run through the opt-in admin container; verifies TLS and unsafe API auth."""
import base64
import ssl
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

context = ssl.create_default_context(cafile='/run/secrets/patroni_ca')
password = Path('/run/secrets/patroni_api_password').read_text().strip()
auth = 'Basic ' + base64.b64encode(('patroni-admin:' + password).encode()).decode()

def request(node, method='GET', path='/health', authorization=None, ctx=context):
    headers = {'Authorization': authorization} if authorization else {}
    req = Request(f'https://pg-node{node}-mgmt:8008{path}',
                  data=b'{}' if method == 'POST' else None,
                  headers=headers, method=method)
    try:
        with urlopen(req, context=ctx, timeout=5) as response:
            return response.status
    except HTTPError as error:
        return error.code

for node in (1, 2, 3):
    assert request(node) == 200
    assert request(node, 'POST', '/reload') == 401
    assert request(node, 'POST', '/reload', 'Basic aW52YWxpZDppbnZhbGlk') == 401
    # Reload configuration without changing cluster settings or leadership.
    assert request(node, 'POST', '/reload', auth) == 202
    try:
        request(node, ctx=ssl.create_default_context())
    except URLError as error:
        assert isinstance(error.reason, ssl.SSLCertVerificationError)
    else:
        raise AssertionError('Untrusted certificate accepted')
print('PASS: all APIs verify against the CA; untrusted TLS fails; unsafe calls require valid credentials.')
