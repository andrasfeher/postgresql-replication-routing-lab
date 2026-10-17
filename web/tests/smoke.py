"""Run against the live app: docker compose exec -T web python3 < web/tests/smoke.py."""
import re
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8080'


def get():
    with urlopen(BASE + '/', timeout=15) as response:
        assert response.status == 200
        return response.read().decode()


def post(path, fields, expected=200):
    request = Request(BASE + path, data=urlencode(fields).encode())
    try:
        with urlopen(request, timeout=15) as response:
            assert response.status == expected
            return response.read().decode()
    except HTTPError as error:
        assert error.code == expected, (error.code, error.read().decode())
        return error.read().decode()


page = get()
token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
title = 'smoke-' + uuid.uuid4().hex + ' <script>alert(1)</script>'
fields = {'csrf_token': token, 'title': title}
post('/add', {'title': title}, 403)
post('/add', {'csrf_token': token, 'title': '   '}, 400)
post('/add', {'csrf_token': token, 'title': 'x' * 501}, 400)
page = post('/add', fields)
assert title not in page
assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
match = next(item for item in re.findall(r'<li.*?</li>', page, re.S) if title.split()[0] in item)
task_id = re.search(r'name="id" value="(\d+)"', match).group(1)
try:
    updated = title.split()[0] + ' updated'
    post('/update', {'csrf_token': token, 'id': task_id, 'title': updated, 'completed': '1'})
    # A separate request and database connection must see the committed update.
    page = get()
    match = next(item for item in re.findall(r'<li.*?</li>', page, re.S) if updated in item)
    assert 'class="done"' in match and ' checked' in match
    page = post('/update', {'csrf_token': token, 'id': task_id, 'title': updated})
    match = next(item for item in re.findall(r'<li.*?</li>', page, re.S) if updated in item)
    assert ' checked' not in match
    post('/update', {'csrf_token': token, 'id': 'invalid', 'title': updated}, 400)
finally:
    post('/delete', {'csrf_token': token, 'id': task_id})
assert title.split()[0] not in get()
post('/delete', {'csrf_token': token, 'id': task_id}, 404)
print('PASS: add, update, complete, reopen, delete, persistence across requests, validation, CSRF, and HTML escaping')
