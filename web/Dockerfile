FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-psycopg2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py init_db.py schema.sql ./
USER nobody
EXPOSE 8080
CMD ["python3", "-u", "app.py"]
