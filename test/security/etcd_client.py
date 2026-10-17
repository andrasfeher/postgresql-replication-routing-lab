"""Run inside a Patroni node after migration. Tests gateway mTLS + scoped RBAC."""
import base64
import json
import ssl
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

base = 'https://etcd1-dcs:2379'
ca = '/run/patroni/etcd-ca.crt'
context = ssl.create_default_context(cafile=ca)
context.load_cert_chain('/run/patroni/etcd-client.crt', '/run/patroni/etcd-client.key')
password = Path('/run/secrets/etcd_patroni_password').read_text().strip()

def call(path, body, token=None, ctx=context):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = token
    request = Request(base + path, data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(request, context=ctx, timeout=5) as response:
            return json.load(response)
    except HTTPError as error:
        return json.load(error)

def encoded(value):
    return base64.b64encode(value.encode()).decode()

response = call('/v3/auth/authenticate', {'name': 'patroni', 'password': password})
assert 'token' in response, response
token = response['token']
assert 'token' not in call('/v3/auth/authenticate', {'name': 'patroni', 'password': 'incorrect'})
key = '/service/postgres-lab/security-check-' + uuid.uuid4().hex
try:
    assert 'error' not in call('/v3/kv/put', {'key': encoded(key), 'value': encoded('test')}, token)
    result = call('/v3/kv/range', {'key': encoded(key)}, token)
    assert base64.b64decode(result['kvs'][0]['value']) == b'test', result
    assert call('/v3/kv/range', {'key': encoded('/outside-patroni')}, token).get('code') == 7
    assert call('/v3/kv/put', {'key': encoded('/security-denied-check'), 'value': encoded('denied')}, token).get('code') == 7
    assert call('/v3/auth/user/list', {}, token).get('code') == 7
    # Gateway clients must authenticate explicitly even with a valid certificate.
    anonymous = call('/v3/kv/range', {'key': encoded(key)})
    assert anonymous.get('code', 0) != 0 and 'kvs' not in anonymous, anonymous
    for path in ['/v3/kv/range', '/v3/auth/authenticate']:
        try:
            call(path, {}, ctx=ssl.create_default_context(cafile=ca))
        except (URLError, OSError):
            pass
        else:
            raise AssertionError('Client without certificate reached etcd')
finally:
    result = call('/v3/kv/deleterange', {'key': encoded(key)}, token)
    assert 'error' not in result, result
print('PASS: etcd mTLS, password authentication, namespace DML, and out-of-scope/admin denials.')
