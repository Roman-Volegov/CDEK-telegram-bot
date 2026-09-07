.PHONY: up down logs check run cleanup-disk install-disk-cleanup

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f bot

check:
	python scripts/check_imports.py

run:
	python -m app.main

cleanup-disk:
	sudo bash scripts/cleanup-disk.sh

install-disk-cleanup:
	sudo bash scripts/install-disk-cleanup.sh
