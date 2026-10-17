"""Run from a non-allowlisted management address, with valid test credentials."""
import base64
import ssl
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

context = ssl.create_default_context(cafile='/run/secrets/patroni_ca')
password = Path('/run/secrets/patroni_api_password').read_text().strip()
auth = 'Basic ' + base64.b64encode(('patroni-admin:' + password).encode()).decode()
for node in (1, 2, 3):
    req = Request(f'https://172.30.80.{10+node}:8008/reload', data=b'{}',
                  headers={'Authorization': auth}, method='POST')
    try:
        urlopen(req, context=context, timeout=5)
    except HTTPError as error:
        assert error.code == 403, error.code
    else:
        raise AssertionError('Non-allowlisted caller was accepted')
print('PASS: unsafe calls from non-allowlisted source denied even with valid credentials.')
