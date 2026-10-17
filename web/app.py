"""A small PostgreSQL-backed task list served with the Python standard library."""

from contextlib import closing
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import os
import threading
from urllib.parse import parse_qs

import psycopg2
from psycopg2 import sql


CSRF_TOKEN = secrets.token_urlsafe(32)
SCHEMA_LOCK = threading.Lock()
schema_ready = False


def connect():
    global schema_ready
    settings = dict(
        password=Path('/run/secrets/postgres_password').read_text().strip(),
        connect_timeout=5, options='-c statement_timeout=5000',
    )
    try:
        connection = psycopg2.connect(**settings)
    except psycopg2.errors.InvalidCatalogName:
        # Bootstrap a fresh lab; existing databases and tasks are preserved.
        with closing(psycopg2.connect(dbname='postgres', **settings)) as admin:
            admin.autocommit = True
            with admin.cursor() as cursor:
                try:
                    cursor.execute(sql.SQL('CREATE DATABASE {}').format(
                        sql.Identifier(os.environ.get('PGDATABASE', 'todo'))))
                except psycopg2.errors.DuplicateDatabase:
                    pass
        connection = psycopg2.connect(**settings)
    try:
        with SCHEMA_LOCK:
            if not schema_ready:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute('''
                            CREATE TABLE IF NOT EXISTS public.tasks (
                                id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                                title text NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
                                completed boolean NOT NULL DEFAULT false,
                                created_at timestamptz NOT NULL DEFAULT now()
                            )
                        ''')
                schema_ready = True
        return connection
    except Exception:
        connection.close()
        raise


STYLE = '''
* { box-sizing: border-box; }
body { margin: 0; background: #f3f5f8; color: #203047; font: 16px system-ui, sans-serif; }
main { max-width: 760px; margin: 64px auto; padding: 32px; background: white;
       border: 1px solid #dce2ea; border-radius: 16px; }
h1 { margin: 0 0 8px; } .subtitle, .empty { color: #637186; }
form { margin: 0; } .add, .edit { display: flex; gap: 10px; align-items: center; }
.add { margin: 28px 0; } input[type=text] { min-width: 0; flex: 1; padding: 12px;
 border: 1px solid #bac5d3; border-radius: 8px; font: inherit; }
button { padding: 12px 16px; border: 0; border-radius: 8px; background: #2459bd;
 color: white; font: inherit; cursor: pointer; } button:hover { filter: brightness(.9); }
ul { padding: 0; list-style: none; } li { padding: 18px 0; border-top: 1px solid #e5e9ef; }
.actions { display: flex; gap: 16px; align-items: center; margin-top: 12px; }
.delete { background: #fff0ef; color: #a52a25; padding: 8px 12px; }
label { display: flex; gap: 8px; align-items: center; } .done input[type=text] { color: #637186; text-decoration: line-through; }
@media(max-width: 600px) { main { margin: 16px; padding: 20px; } .edit { flex-wrap: wrap; } }
'''


def token_field():
    return f'<input type="hidden" name="csrf_token" value="{CSRF_TOKEN}">'


class Handler(BaseHTTPRequestHandler):
    def page(self, status, content):
        body = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Task list</title><style>{STYLE}</style></head>'
            f'<body><main>{content}</main></body></html>'
        ).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def unavailable(self):
        self.page(503, '<h1>Database unavailable</h1><p>Please refresh to retry. '
                  'If you were saving a task, check the list before submitting again.</p>')

    def do_GET(self):
        if self.path != '/':
            self.send_error(404)
            return
        try:
            with closing(connect()) as connection:
                with connection:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT id, title, completed FROM public.tasks ORDER BY id DESC')
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
                    {token_field()}<input type="hidden" name="id" value="{task_id}">
                    <div class="edit">
                      <input type="text" name="title" value="{escape(title, quote=True)}"
                             aria-label="Task title" required maxlength="500">
                      <button type="submit">Save</button>
                    </div>
                    <div class="actions"><label><input type="checkbox" name="completed" value="1"{checked}>Completed</label></div>
                  </form>
                  <div class="actions"><form method="post" action="/delete">
                    {token_field()}<input type="hidden" name="id" value="{task_id}">
                    <button class="delete" type="submit">Delete</button>
                  </form></div>
                </li>''')
        remaining = sum(not task[2] for task in tasks)
        listing = '<ul>' + ''.join(items) + '</ul>' if tasks else '<p class="empty">No tasks yet. Add your first task above.</p>'
        self.page(200, f'''<h1>Task list</h1>
            <p class="subtitle">{remaining} remaining · {len(tasks)} total · Stored on PostgreSQL</p>
            <form class="add" method="post" action="/add">{token_field()}
              <input type="text" name="title" placeholder="What needs doing?" aria-label="New task" required maxlength="500">
              <button type="submit">Add task</button>
            </form>{listing}''')

    def do_POST(self):
        if self.path not in ('/add', '/update', '/delete'):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8192:
                raise ValueError('Invalid form size')
            form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
            if not secrets.compare_digest(form.get('csrf_token', [''])[0].encode(), CSRF_TOKEN.encode()):
                self.page(403, '<h1>Form expired</h1><p><a href="/">Reload the task list</a> and try again.</p>')
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
                            cursor.execute('INSERT INTO public.tasks (title) VALUES (%s)', (title,))
                        elif self.path == '/update':
                            cursor.execute('UPDATE public.tasks SET title = %s, completed = %s WHERE id = %s',
                                           (title, form.get('completed') == ['1'], task_id))
                        else:
                            cursor.execute('DELETE FROM public.tasks WHERE id = %s', (task_id,))
                        if self.path != '/add' and cursor.rowcount == 0:
                            self.page(404, '<h1>Task no longer exists</h1><p><a href="/">Return to the task list</a>.</p>')
                            return
        except (ValueError, UnicodeError) as error:
            self.page(400, f'<h1>Invalid task</h1><p>{escape(str(error))}</p><p><a href="/">Return to the task list</a>.</p>')
            return
        except (OSError, psycopg2.Error):
            self.unavailable()
            return
        self.send_response(303)
        self.send_header('Location', '/')
        self.send_header('Content-Length', '0')
        self.end_headers()


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
