"""Display the current primary's PostgreSQL version."""

from contextlib import closing
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg2


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return

        try:
            password = Path("/run/secrets/postgres_password").read_text().strip()
            # libpq reads the connection endpoint and user from PG* environment variables.
            # A fresh connection on every request follows HAProxy after failover.
            with closing(psycopg2.connect(password=password, connect_timeout=5,
                                         options="-c statement_timeout=5000")) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT version(), pg_is_in_recovery()")
                    version, in_recovery = cursor.fetchone()
                    if in_recovery:
                        raise RuntimeError("The database endpoint reached a replica")
            status = 200
            content = f"<h1>PostgreSQL primary</h1><p>{escape(version)}</p>"
        except (OSError, psycopg2.Error, RuntimeError):
            status = 503
            content = "<h1>PostgreSQL primary unavailable</h1><p>Please refresh to retry.</p>"

        body = (
            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '<title>PostgreSQL primary version</title></head>'
            f"<body>{content}</body></html>"
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
