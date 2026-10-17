"""A small PostgreSQL-backed task list served with the Python standard library."""

from contextlib import closing
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import ipaddress
import hashlib
import hmac
import re
from http.cookies import SimpleCookie
import os
import threading
import time
import smtplib
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

import psycopg2


CSRF_TOKEN = secrets.token_urlsafe(32)
AUTH_LOCK = threading.Lock()
AUTH_ATTEMPTS = {}


def connect():
    return psycopg2.connect(
        user='webapp', dbname='todo',
        password=Path('/run/secrets/webapp_password').read_text().strip(),
        connect_timeout=5,
    )


STYLE = '''
* { box-sizing: border-box; }
body { margin: 0; background: #f3f5f8; color: #203047; font: 16px system-ui, sans-serif; }
main { max-width: 760px; margin: 64px auto; padding: 32px; background: white;
       border: 1px solid #dce2ea; border-radius: 16px; }
h1 { margin: 0 0 8px; } .subtitle, .empty { color: #637186; }
form { margin: 0; } .add, .edit { display: flex; gap: 10px; align-items: center; }
.add { margin: 28px 0; } input[type=text], input[type=email], input[type=password] { min-width: 0; flex: 1; padding: 12px;
 border: 1px solid #bac5d3; border-radius: 8px; font: inherit; }
button { padding: 12px 16px; border: 0; border-radius: 8px; background: #2459bd;
 color: white; font: inherit; cursor: pointer; } button:hover { filter: brightness(.9); }
ul { padding: 0; list-style: none; } li { padding: 18px 0; border-top: 1px solid #e5e9ef; }
.actions { display: flex; gap: 16px; align-items: center; margin-top: 12px; }
.delete { background: #fff0ef; color: #a52a25; padding: 8px 12px; }
label { display: flex; gap: 8px; align-items: center; } .done input[type=text] { color: #637186; text-decoration: line-through; }
@media(max-width: 600px) { main { margin: 16px; padding: 20px; } .edit { flex-wrap: wrap; } }
'''


def token_field(token):
    return f'<input type="hidden" name="csrf_token" value="{token}">'


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 600000).hex()
    return f'{salt}${digest}'


def valid_email(value):
    if len(value) > 254 or value.count('@') != 1:
        return False
    local, domain = value.split('@')
    return (1 <= len(local) <= 64 and not local.startswith('.') and not local.endswith('.')
            and '..' not in local and re.fullmatch(r"[a-z0-9.!#$%&'*+/=?^_`{|}~-]+", local)
            and len(domain.split('.')) >= 2
            and all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', part)
                    for part in domain.split('.')) and not domain.split('.')[-1].isdigit())


DUMMY_HASH = password_hash('dummy-password-for-timing')


class BoundedHTTPServer(ThreadingHTTPServer):
    max_requests = 64
    request_timeout = 10

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(self.max_requests)
        super().__init__(*args, **kwargs)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(self.request_timeout)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def client_ip(self):
        address = self.client_address[0]
        if address == os.environ.get('TRUSTED_PROXY_IP'):
            try:
                return str(ipaddress.ip_address(self.headers.get('X-Real-IP', '')))
            except ValueError:
                pass
        return address

    def prepare(self):
        self.cookies_out = []
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get('Cookie', ''))
        except Exception:
            pass
        self.session = cookies['session'].value if 'session' in cookies else ''
        nonce = cookies['form_nonce'].value if 'form_nonce' in cookies else ''
        if not nonce or len(nonce) > 128:
            nonce = secrets.token_urlsafe(32)
            self.cookies_out.append(f'form_nonce={nonce}; HttpOnly; SameSite=Strict; Path=/')
        self.csrf = hmac.new(CSRF_TOKEN.encode(), (self.session or nonce).encode(), 'sha256').hexdigest()

    def redirect(self, path):
        self.send_response(303)
        self.send_header('Location', path)
        for cookie in self.cookies_out:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def user(self):
        if not self.session or len(self.session) > 128:
            return None
        with closing(connect()) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT u.id, u.email FROM public.users u
                    JOIN public.sessions s ON s.user_id = u.id
                    WHERE s.token_hash = %s AND s.expires_at > now() AND u.email_verified""",
                    (hashlib.sha256(self.session.encode()).hexdigest(),))
                return cursor.fetchone()

    def auth_page(self, registration=False, error='', status=200):
        heading = 'Register' if registration else 'Log in'
        action = '/register' if registration else '/login'
        link = '<a href="/login">Already registered? Log in</a>' if registration else '<a href="/register">Create an account</a>'
        autocomplete = 'new-password' if registration else 'current-password'
        self.page(status, f'''<h1>{heading}</h1>
            <p role="alert">{escape(error)}</p>
            <form method="post" action="{action}">{token_field(self.csrf)}
              <p><label>Email address <input type="email" name="email" required maxlength="254" autocomplete="email"></label></p>
              <p><label>Password <input type="password" name="password" required minlength="12" maxlength="128" autocomplete="{autocomplete}"></label></p>
              <p class="subtitle">Use 12–128 characters for your password. Verify your email before logging in.</p>
              <button type="submit">{heading}</button>
            </form><p>{link}</p>''')

    def authenticate(self, form):
        registration = self.path == '/register'
        with AUTH_LOCK:
            cutoff = time.monotonic() - 60
            for address in list(AUTH_ATTEMPTS):
                AUTH_ATTEMPTS[address] = [attempt for attempt in AUTH_ATTEMPTS[address] if attempt > cutoff]
                if not AUTH_ATTEMPTS[address]:
                    del AUTH_ATTEMPTS[address]
            attempts = AUTH_ATTEMPTS.setdefault(self.client_ip(), [])
            if len(attempts) >= 20:
                self.auth_page(registration, 'Too many attempts. Please wait a minute and retry.', 429)
                return
            attempts.append(time.monotonic())
        email = form.get('email', [''])[0].strip().lower()
        password = form.get('password', [''])[0]
        if not valid_email(email):
            self.auth_page(registration, 'Enter a valid email address.', 400)
            return
        if not 12 <= len(password) <= 128:
            self.auth_page(registration, 'Password must contain 12–128 characters.', 400)
            return
        with closing(connect()) as connection:
            with connection:
                with connection.cursor() as cursor:
                    if registration:
                        cursor.execute('INSERT INTO public.users (email, password_hash) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING RETURNING id',
                                       (email, password_hash(password)))
                        row = cursor.fetchone()
                        if not row:
                            self.auth_page(True, 'This account already exists. Try logging in', 409)
                            return
                        user_id = row[0]
                    else:
                        cursor.execute('SELECT id, password_hash, email_verified FROM public.users WHERE email = %s', (email,))
                        row = cursor.fetchone()
                        stored = row[1] if row else DUMMY_HASH
                        matches = hmac.compare_digest(password_hash(password, stored.split('$')[0]), stored)
                        if not row or not matches:
                            self.auth_page(False, 'Invalid email address or password.', 401)
                            return
                        user_id = row[0]
                    if registration or not row[2]:
                        self.send_verification(cursor, user_id, email)
                        # Commit the pending account and token before showing success.
                        connection.commit()
                        self.page(200, '<h1>Check your email</h1><p>Open the verification link in your email to activate your account. '
                                  'The link expires in one hour.</p><p><a href="/login">Return to login</a></p>')
                        return
                    cursor.execute('DELETE FROM public.sessions WHERE expires_at <= now() OR token_hash = %s',
                                   (hashlib.sha256(self.session.encode()).hexdigest(),))
                    session = secrets.token_urlsafe(32)
                    cursor.execute('INSERT INTO public.sessions (token_hash, user_id) VALUES (%s, %s)',
                                   (hashlib.sha256(session.encode()).hexdigest(), user_id))
        self.cookies_out.append(f'session={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400')
        self.redirect('/')

    def send_verification(self, cursor, user_id, email):
        cursor.execute("SELECT 1 FROM public.email_verifications WHERE user_id = %s AND created_at > now() - interval '1 minute'", (user_id,))
        if cursor.fetchone():
            return
        token = secrets.token_urlsafe(32)
        cursor.execute('DELETE FROM public.email_verifications WHERE user_id = %s', (user_id,))
        cursor.execute('INSERT INTO public.email_verifications (token_hash, user_id) VALUES (%s, %s)',
                       (hashlib.sha256(token.encode()).hexdigest(), user_id))
        link = os.environ.get('APP_BASE_URL', 'http://127.0.0.1:8080').rstrip('/') + '/verify?token=' + token
        message = EmailMessage()
        message['From'] = os.environ.get('MAIL_FROM', 'todo@example.test')
        message['To'] = email
        message['Subject'] = 'Verify your task list email address'
        message.set_content('Confirm your email address to activate your task list account.\n\n'
                            + link + '\n\nThis link expires in one hour. If you did not register, ignore this email.')
        with smtplib.SMTP(os.environ.get('SMTP_HOST', 'mailpit'), int(os.environ.get('SMTP_PORT', '1025')), timeout=10) as mail:
            mail.send_message(message)

    def verify_email(self, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
            self.page(400, '<h1>Invalid verification link</h1>')
            return
        with closing(connect()) as connection:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM public.email_verifications WHERE token_hash = %s AND expires_at > now() RETURNING user_id",
                                   (hashlib.sha256(token.encode()).hexdigest(),))
                    row = cursor.fetchone()
                    if not row:
                        self.page(400, '<h1>Link expired or already used</h1><p>Log in with your email and password to request a new link.</p><a href="/login">Log in</a>')
                        return
                    cursor.execute('UPDATE public.users SET email_verified = true WHERE id = %s', (row[0],))
        self.page(200, '<h1>Email verified</h1><p>Your account is ready.</p><a href="/login">Log in</a>')

    def log_message(self, format, *args):
        # Verification tokens must not appear in request logs.
        if urlsplit(self.path).path == '/verify':
            return
        super().log_message(format, *args)

    def page(self, status, content):
        body = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Task list</title><style>{STYLE}</style></head>'
            f'<body><main>{content}</main></body></html>'
        ).encode('utf-8')
        self.send_response(status)
        for cookie in self.cookies_out:
            self.send_header('Set-Cookie', cookie)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def unavailable(self):
        self.page(503, '<h1>Database unavailable</h1><p>Please refresh to retry. '
                  'If you were saving a task, check the list before submitting again.</p>')

    def do_GET(self):
        self.prepare()
        if urlsplit(self.path).path == '/verify':
            token = parse_qs(urlsplit(self.path).query).get('token', [''])[0]
            if not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
                self.page(400, '<h1>Invalid verification link</h1>')
                return
            self.page(200, f'''<h1>Verify email address</h1><p>Confirm to activate your account.</p>
                <form method="post" action="/verify">{token_field(self.csrf)}
                  <input type="hidden" name="token" value="{escape(token, quote=True)}">
                  <button type="submit">Verify email</button>
                </form>''')
            return
        if self.path not in ('/', '/login', '/register'):
            self.send_error(404)
            return
        try:
            user = self.user()
            if self.path in ('/login', '/register'):
                if user:
                    self.redirect('/')
                else:
                    self.auth_page(self.path == '/register')
                return
            if not user:
                self.redirect('/login')
                return
            with closing(connect()) as connection:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT id, title, completed FROM public.tasks WHERE user_id = %s ORDER BY id DESC', (user[0],))
                        tasks = cursor.fetchall()
        except (OSError, psycopg2.Error):
            self.unavailable()
            return

        items = []
        for task_id, title, completed in tasks:
            checked = ' checked' if completed else ''
            css_class = ' class="done"' if completed else ''
            items.append(f'''
                <li{css_class}>
                  <form method="post" action="/update">
                    {token_field(self.csrf)}<input type="hidden" name="id" value="{task_id}">
                    <div class="edit">
                      <input type="text" name="title" value="{escape(title, quote=True)}"
                             aria-label="Task title" required maxlength="500">
                      <button type="submit">Save</button>
                    </div>
                    <div class="actions"><label><input type="checkbox" name="completed" value="1"{checked}>Completed</label></div>
                  </form>
                  <div class="actions"><form method="post" action="/delete">
                    {token_field(self.csrf)}<input type="hidden" name="id" value="{task_id}">
                    <button class="delete" type="submit">Delete</button>
                  </form></div>
                </li>''')
        remaining = sum(not task[2] for task in tasks)
        listing = '<ul>' + ''.join(items) + '</ul>' if tasks else '<p class="empty">No tasks yet. Add your first task above.</p>'
        self.page(200, f'''<h1>My tasks</h1><p>Signed in as {escape(user[1])}</p>
            <form method="post" action="/logout">{token_field(self.csrf)}<button type="submit">Log out</button></form>
            <p class="subtitle">{remaining} remaining · {len(tasks)} total · Stored on PostgreSQL</p>
            <form class="add" method="post" action="/add">{token_field(self.csrf)}
              <input type="text" name="title" placeholder="What needs doing?" aria-label="New task" required maxlength="500">
              <button type="submit">Add task</button>
            </form>{listing}''')

    def do_POST(self):
        self.prepare()
        if self.path not in ('/add', '/update', '/delete', '/login', '/register', '/logout', '/verify'):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Invalid form size')
            form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
            if not secrets.compare_digest(form.get('csrf_token', [''])[0].encode(), self.csrf.encode()):
                self.page(403, '<h1>Form expired</h1><p><a href="/">Reload the task list</a> and try again.</p>')
                return
            if self.path == '/verify':
                self.verify_email(form.get('token', [''])[0])
                return
            if self.path in ('/login', '/register'):
                self.authenticate(form)
                return
            user = self.user()
            if not user:
                self.redirect('/login')
                return
            if self.path == '/logout':
                with closing(connect()) as connection:
                    with connection:
                        with connection.cursor() as cursor:
                            cursor.execute('DELETE FROM public.sessions WHERE token_hash = %s',
                                           (hashlib.sha256(self.session.encode()).hexdigest(),))
                self.cookies_out.append('session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                self.redirect('/login')
                return
            title = form.get('title', [''])[0].strip()
            if self.path != '/delete' and not 1 <= len(title) <= 500:
                raise ValueError('Task titles must contain 1–500 characters')
            task_id = int(form.get('id', ['0'])[0])
            if self.path != '/add' and task_id <= 0:
                raise ValueError('Invalid task ID')
            with closing(connect()) as connection:
                with connection:
                    with connection.cursor() as cursor:
                        if self.path == '/add':
                            cursor.execute('INSERT INTO public.tasks (title, user_id) VALUES (%s, %s)', (title, user[0]))
                        elif self.path == '/update':
                            cursor.execute('UPDATE public.tasks SET title = %s, completed = %s WHERE id = %s AND user_id = %s',
                                           (title, form.get('completed') == ['1'], task_id, user[0]))
                        else:
                            cursor.execute('DELETE FROM public.tasks WHERE id = %s AND user_id = %s', (task_id, user[0]))
                        if self.path != '/add' and cursor.rowcount == 0:
                            self.page(404, '<h1>Task no longer exists</h1><p><a href="/">Return to the task list</a>.</p>')
                            return
        except (ValueError, UnicodeError) as error:
            self.page(400, f'<h1>Invalid task</h1><p>{escape(str(error))}</p><p><a href="/">Return to the task list</a>.</p>')
            return
        except (OSError, smtplib.SMTPException):
            self.page(503, '<h1>Email service unavailable</h1><p>Please try again shortly. Existing accounts can log in to request another verification email.</p>')
            return
        except psycopg2.Error:
            self.unavailable()
            return
        self.redirect('/')


if __name__ == '__main__':
    BoundedHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
