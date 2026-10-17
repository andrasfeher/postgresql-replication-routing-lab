"""One-time migration. Stop web, then run this using its image and password secret."""
from contextlib import closing
from pathlib import Path
import psycopg2

settings = dict(password=Path('/run/secrets/postgres_password').read_text().strip(),
                connect_timeout=5, options='-c statement_timeout=30000')
with closing(psycopg2.connect(dbname='postgres', **settings)) as admin:
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = 'todo'")
        if not cursor.fetchone():
            cursor.execute('CREATE DATABASE todo')

with closing(psycopg2.connect(dbname='postgres', **settings)) as source:
    with source:
        with source.cursor() as old:
            old.execute("SELECT to_regclass('public.todo_tasks')")
            if old.fetchone()[0] is None:
                raise SystemExit('No public.todo_tasks source table; nothing migrated.')
            old.execute('LOCK TABLE public.todo_tasks IN ACCESS EXCLUSIVE MODE')
            old.execute('SELECT id, title, completed, created_at FROM public.todo_tasks ORDER BY id')
            rows = old.fetchall()
            old.execute('SELECT last_value, is_called FROM public.todo_tasks_id_seq')
            sequence_value, sequence_called = old.fetchone()
            with closing(psycopg2.connect(dbname='todo', **settings)) as target:
                with target:
                    with target.cursor() as new:
                        new.execute('''CREATE TABLE IF NOT EXISTS public.tasks (
                            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
                            completed boolean NOT NULL DEFAULT false,
                            created_at timestamptz NOT NULL DEFAULT now())''')
                        new.executemany('''INSERT INTO public.tasks (id, title, completed, created_at)
                            OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s, %s)
                            ON CONFLICT (id) DO NOTHING''', rows)
                        new.execute('SELECT id, title, completed, created_at FROM public.tasks ORDER BY id')
                        if new.fetchall() != rows:
                            raise RuntimeError('Destination differs from source; original table preserved')
                        new.execute("SELECT setval(pg_get_serial_sequence('public.tasks', 'id'), %s, %s)",
                                    (sequence_value, sequence_called))
                # Verify committed destination data before dropping the source.
                with target.cursor() as new:
                    new.execute('SELECT id, title, completed, created_at FROM public.tasks ORDER BY id')
                    if new.fetchall() != rows:
                        raise RuntimeError('Verification failed; original table preserved')
            old.execute('DROP TABLE public.todo_tasks')
print(f'Migrated and verified {len(rows)} tasks into todo.public.tasks; removed postgres.public.todo_tasks.')
