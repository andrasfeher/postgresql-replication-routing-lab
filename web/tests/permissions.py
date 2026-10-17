"""Run inside web: python3 < web/tests/permissions.py. All DML is rolled back."""
from pathlib import Path
import uuid
import psycopg2

db = psycopg2.connect(user='webapp', dbname='todo',
    password=Path('/run/secrets/webapp_password').read_text().strip())
try:
    with db.cursor() as cursor:
        cursor.execute('SELECT current_user')
        assert cursor.fetchone()[0] == 'webapp'
        cursor.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        assert cursor.fetchone() == (False,) * 5
        cursor.execute("SELECT has_database_privilege(current_user, 'todo', 'CREATE'), has_database_privilege(current_user, 'todo', 'TEMP'), has_schema_privilege(current_user, 'public', 'CREATE')")
        assert cursor.fetchone() == (False,) * 3
        cursor.execute("INSERT INTO public.users(email, password_hash) VALUES (%s, 'test') RETURNING id",
                       ('permissions-' + uuid.uuid4().hex + '@example.test',))
        user_id = cursor.fetchone()[0]
        cursor.execute("INSERT INTO public.tasks(title, user_id) VALUES ('permission test', %s) RETURNING id", (user_id,))
        task_id = cursor.fetchone()[0]
        cursor.execute("UPDATE public.tasks SET title = 'updated' WHERE id = %s", (task_id,))
        cursor.execute('SELECT title FROM public.tasks WHERE id = %s', (task_id,))
        assert cursor.fetchone()[0] == 'updated'
        cursor.execute('DELETE FROM public.tasks WHERE id = %s', (task_id,))
        assert cursor.rowcount == 1
    db.rollback()
    for statement in (
        'CREATE TABLE public.denied_ddl_test(id int)',
        'CREATE TEMP TABLE denied_ddl_test(id int)',
        'CREATE SCHEMA denied_ddl_test',
        'ALTER TABLE public.tasks ADD COLUMN denied_ddl_test int',
        'DROP TABLE public.tasks',
        'TRUNCATE public.tasks',
    ):
        try:
            with db.cursor() as cursor:
                cursor.execute(statement)
        except psycopg2.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError('Unexpected permission: ' + statement)
        finally:
            db.rollback()
    print('PASS: webapp DML/sequence access works; CREATE/ALTER/DROP/TRUNCATE denied.')
finally:
    db.close()
