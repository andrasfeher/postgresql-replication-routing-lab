"""Run inside web to verify the application's deployed RW pooler path."""
from contextlib import closing
from pathlib import Path
import psycopg2
from app import connect

with closing(connect()) as connection:
    parameters = connection.get_dsn_parameters()
    assert parameters['host'] == 'pgbouncer-1', parameters['host']
    assert parameters['port'] == '6432', parameters['port']
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_user, current_database(), pg_is_in_recovery()')
        assert cursor.fetchone() == ('webapp', 'todo', False)
        cursor.execute('SHOW statement_timeout')
        assert cursor.fetchone()[0] == '5s'

try:
    connection = psycopg2.connect(host='pgbouncer-1', port=6432, user='webapp',
        dbname='pgbouncer', connect_timeout=5,
        password=Path('/run/secrets/webapp_password').read_text().strip())
except psycopg2.OperationalError as error:
    assert 'not allowed' in str(error).lower(), str(error)
else:
    connection.close()
    raise AssertionError('webapp unexpectedly accessed the PgBouncer admin console')
print('PASS: app connects through RW PgBouncer as webapp to the primary; 5s timeout and admin isolation retained.')
