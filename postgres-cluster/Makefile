.PHONY: help secrets up down reset logs ps verify check

help:
	@printf '%s\n' \
	  'make secrets  Generate local Docker secret files' \
	  'make up       Build and start the lab' \
	  'make ps       Show container status' \
	  'make verify   Verify roles, replication and RW/RO endpoints' \
	  'make logs     Follow all service logs' \
	  'make check    Validate shell, Compose and HAProxy configuration' \
	  'make down     Stop the lab, preserving volumes' \
	  'make reset    Stop the lab and delete all lab volumes/data'

secrets:
	./scripts/init-secrets.sh

up: secrets
	docker compose up -d --build

ps:
	docker compose ps

verify:
	./scripts/verify.sh

logs:
	docker compose logs -f

check:
	./scripts/check.sh

down:
	docker compose down

reset:
	docker compose down -v --remove-orphans
