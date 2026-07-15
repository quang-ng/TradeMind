.PHONY: up down test lint migrate

up:
	docker compose up -d --build

down:
	docker compose down

test:
	uv run pytest

lint:
	uv run ruff check .

migrate:
	uv run alembic upgrade head
