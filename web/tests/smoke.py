"""Run against the live app: docker compose exec -T web python3 < web/tests/smoke.py."""
import re
import uuid
import json
import time
from http.cookiejar import CookieJar
from urllib.request import build_opener, HTTPCookieProcessor
from pathlib import Path
from contextlib import closing
import psycopg2
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = 'http://127.0.0.1:8080'
jar = CookieJar()
opener = build_opener(HTTPCookieProcessor(jar))
urlopen = opener.open


def get(path='/'):
    with urlopen(BASE + path, timeout=15) as response:
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


def csrf(page):
    return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)


def verify_registration(page, address, expire_first=False):
    assert 'Check your email' in page and 'Signed in as' not in page
    assert 'Log in' in get()
    token = csrf(get('/login'))
    page = post('/login', {'csrf_token': token, 'email': address, 'password': password})
    assert 'Check your email' in page
    with build_opener().open('http://mailpit:8025/api/v1/message/latest', timeout=15) as response:
        message = json.load(response)
    assert message['To'][0]['Address'] == address
    link = re.search(r'http://127.0.0.1:8080/verify\?token=[A-Za-z0-9_-]+', message['Text']).group(0)
    verification_token = link.split('token=')[1]
    with closing(psycopg2.connect(password=Path('/run/secrets/postgres_password').read_text().strip())) as db:
        with db:
            with db.cursor() as cursor:
                cursor.execute('SELECT v.token_hash, u.email_verified FROM public.email_verifications v JOIN public.users u ON u.id=v.user_id WHERE u.email=%s', (address,))
                stored, verified = cursor.fetchone()
                assert stored != verification_token and not verified
                if expire_first:
                    cursor.execute("UPDATE public.email_verifications SET expires_at=now()-interval '1 second', created_at=now()-interval '2 minutes' WHERE user_id=(SELECT id FROM public.users WHERE email=%s)", (address,))
    page = get(link.removeprefix(BASE))
    fields = {'csrf_token': csrf(page), 'token': verification_token}
    if expire_first:
        post('/verify', fields, 400)
        page = post('/login', {'csrf_token': csrf(get('/login')), 'email': address, 'password': password})
        assert 'Check your email' in page
        with build_opener().open('http://mailpit:8025/api/v1/message/latest', timeout=15) as response:
            message = json.load(response)
        new_token = re.search(r'/verify\?token=([A-Za-z0-9_-]+)', message['Text']).group(1)
        assert new_token != verification_token
        verification_token = new_token
        fields = {'csrf_token': csrf(get('/verify?token=' + new_token)), 'token': new_token}
    post('/verify', {'token': verification_token}, 403)
    assert 'Email verified' in post('/verify', fields)
    post('/verify', fields, 400)
    return post('/login', {'csrf_token': csrf(get('/login')), 'email': address, 'password': password})


def run():
    global urlopen
    page = get()
    assert 'Log in' in page and 'Create an account' in page
    token = csrf(page)
    post('/add', {'csrf_token': token, 'title': 'unauthorized'})
    assert 'Log in' in get()
    post('/register', {'csrf_token': token, 'email': 'bad..email@example.com', 'password': password}, 400)
    post('/register', {'csrf_token': token, 'email': email, 'password': 'short'}, 400)
    page = post('/register', {'csrf_token': token, 'email': email.upper(), 'password': password})
    page = verify_registration(page, email)
    assert 'Signed in as ' + email in page
    session = next(cookie.value for cookie in jar if cookie.name == 'session')
    with closing(psycopg2.connect(password=Path('/run/secrets/postgres_password').read_text().strip())) as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema='public' AND table_name='tasks' AND column_name='user_id'")
            assert cursor.fetchone()[0] == 'NO'
            cursor.execute('SELECT count(*) FROM public.tasks WHERE user_id IS NULL')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT password_hash FROM public.users WHERE email = %s', (email,))
            stored = cursor.fetchone()[0]
            assert stored != password and '$' in stored
            cursor.execute('SELECT token_hash FROM public.sessions WHERE user_id = (SELECT id FROM public.users WHERE email = %s)', (email,))
            assert cursor.fetchone()[0] != session
    token = csrf(page)
    post('/logout', {'csrf_token': token})
    page = get()
    assert 'Log in' in page
    token = csrf(page)
    post('/register', {'csrf_token': token, 'email': email, 'password': password}, 409)
    post('/login', {'csrf_token': token, 'email': email, 'password': 'wrong-password-123'}, 401)
    page = post('/login', {'csrf_token': token, 'email': email, 'password': password})
    assert 'Signed in as ' + email in page
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
        first_client = urlopen
        urlopen = build_opener(HTTPCookieProcessor(CookieJar())).open
        try:
            other_token = csrf(get('/register'))
            other_page = post('/register', {'csrf_token': other_token, 'email': other_email, 'password': password})
            other_page = verify_registration(other_page, other_email, expire_first=True)
            assert title.split()[0] not in other_page
            other_token = csrf(other_page)
            post('/update', {'csrf_token': other_token, 'id': task_id, 'title': 'stolen'}, 404)
            post('/delete', {'csrf_token': other_token, 'id': task_id}, 404)
            # An explicitly supplied owner is ignored; ownership comes from the session.
            with closing(psycopg2.connect(password=Path('/run/secrets/postgres_password').read_text().strip())) as db:
                with db.cursor() as cursor:
                    cursor.execute('SELECT id FROM public.users WHERE email = %s', (email,))
                    first_user_id = cursor.fetchone()[0]
            other_title = 'private-' + uuid.uuid4().hex
            other_page = post('/add', {'csrf_token': other_token, 'title': other_title, 'user_id': first_user_id})
            assert other_title in other_page
        finally:
            urlopen = first_client
        assert other_title not in get()
        assert title.split()[0] in get()
        print('PASS: separate user lists, cross-user update/delete blocked, forged ownership ignored')
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
    with closing(psycopg2.connect(password=Path('/run/secrets/postgres_password').read_text().strip())) as db:
        with db:
            with db.cursor() as cursor:
                cursor.execute("UPDATE public.sessions SET expires_at = now() - interval '1 second' WHERE user_id = (SELECT id FROM public.users WHERE email = %s)", (email,))
    assert 'Log in' in get()
    print('PASS: email delivery, verification, expired links, resend, replay prevention, and unverified login blocked')
    print('PASS: login, registration, duplicate/invalid email, password hashing, session hashing, logout, and expiry')


email = 'smoke-' + uuid.uuid4().hex + '@example.invalid'
other_email = 'smoke-other-' + uuid.uuid4().hex + '@example.invalid'
password = 'smoke-password-' + uuid.uuid4().hex
try:
    for attempt in range(30):
        try:
            get('/login')
            break
        except OSError:
            if attempt == 29:
                raise
            time.sleep(0.2)
    run()
finally:
    with closing(psycopg2.connect(password=Path('/run/secrets/postgres_password').read_text().strip())) as db:
        with db:
            with db.cursor() as cursor:
                cursor.execute('DELETE FROM public.users WHERE email IN (%s, %s)', (email, other_email))
