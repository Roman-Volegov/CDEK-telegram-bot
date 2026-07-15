.PHONY: up down logs check run

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
