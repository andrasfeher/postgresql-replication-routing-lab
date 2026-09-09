FROM debian:trixie-slim

ARG PG_MAJOR=18
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates \
       curl \
       postgresql-common \
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
    && echo 'create_main_cluster = false' > /etc/postgresql-common/createcluster.conf \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
       "postgresql-${PG_MAJOR}" \
       "postgresql-client-${PG_MAJOR}" \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/node1-entrypoint.sh /usr/local/bin/node1-entrypoint.sh
COPY scripts/node2-entrypoint.sh /usr/local/bin/node2-entrypoint.sh
COPY scripts/node3-entrypoint.sh /usr/local/bin/node3-entrypoint.sh

RUN chmod 0755 \
    /usr/local/bin/node1-entrypoint.sh \
    /usr/local/bin/node2-entrypoint.sh \
    /usr/local/bin/node3-entrypoint.sh
