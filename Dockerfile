FROM debian:trixie-slim

ARG PG_MAJOR=18
ENV DEBIAN_FRONTEND=noninteractive

# Patroni will be installed into this isolated Python environment.
ENV PATRONI_VENV=/opt/patroni
ENV PATH="${PATRONI_VENV}/bin:${PATH}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       postgresql-common \
       python3 \
       python3-venv \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl --fail --silent --show-error \
       -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && printf '%s\n' \
       'Types: deb' \
       'URIs: https://apt.postgresql.org/pub/repos/apt' \
       "Suites: ${VERSION_CODENAME}-pgdg" \
       'Components: main' \
       'Signed-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc' \
       > /etc/apt/sources.list.d/pgdg.sources \
    && echo 'create_main_cluster = false' \
       > /etc/postgresql-common/createcluster.conf \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       "postgresql-${PG_MAJOR}" \
       "postgresql-client-${PG_MAJOR}" \
    && python3 -m venv "${PATRONI_VENV}" \
    && "${PATRONI_VENV}/bin/pip" install --no-cache-dir \
       "patroni[etcd3,psycopg3]" \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/patroni-entrypoint.sh \
     /usr/local/bin/patroni-entrypoint.sh

RUN chmod +x /usr/local/bin/patroni-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/patroni-entrypoint.sh"]