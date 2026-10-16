FROM python:3.13-slim

RUN apt-get update && \
    apt-get install -y \
        openssh-client \
        git \
        curl \
        docker.io \
        && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    ansible-core \
    "psycopg[binary]" \
    docker

WORKDIR /ansible

COPY requirements.yml .

RUN ansible-galaxy collection install \
    -r requirements.yml

CMD ["sleep", "infinity"]
