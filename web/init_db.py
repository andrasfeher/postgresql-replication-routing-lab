"""Run schema changes and grants with administrator credentials, never in web."""
from contextlib import closing
from pathlib import Path
import time

import psycopg2


def initialize():
    settings = dict(user='postgres',
                    password=Path('/run/secrets/postgres_password').read_text().strip(),
                    connect_timeout=5)
    with closing(psycopg2.connect(dbname='postgres', **settings)) as admin:
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = 'todo'")
            if not cursor.fetchone():
                cursor.execute('CREATE DATABASE todo OWNER postgres')
    with closing(psycopg2.connect(dbname='todo', **settings)) as connection:
        with connection:
            with connection.cursor() as cursor:
                cursor.execute(Path('/app/schema.sql').read_text())
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = 'webapp'")
                if not cursor.fetchone():
                    cursor.execute('CREATE ROLE webapp LOGIN')
                cursor.execute('''ALTER ROLE webapp WITH LOGIN NOSUPERUSER NOCREATEDB
                                  NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT
                                  PASSWORD %s''',
                               (Path('/run/secrets/webapp_password').read_text().strip(),))
                # PUBLIC privileges are inherited even with NOINHERIT.
                cursor.execute('''
                    REVOKE CREATE, TEMPORARY ON DATABASE todo FROM PUBLIC, webapp;
                    GRANT CONNECT ON DATABASE todo TO webapp;
                    REVOKE CREATE ON SCHEMA public FROM PUBLIC, webapp;
                    GRANT USAGE ON SCHEMA public TO webapp;
                    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM webapp;
                    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO webapp;
                    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM webapp;
                    GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO webapp;
                    ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
                        GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO webapp;
                    ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public
                        GRANT USAGE ON SEQUENCES TO webapp;
                ''')
    print('todo schema initialized; webapp has DML access without schema creation privileges.')


if __name__ == '__main__':
    for attempt in range(30):
        try:
            initialize()
            break
        except psycopg2.OperationalError:
            if attempt == 29:
                raise
            time.sleep(2)
