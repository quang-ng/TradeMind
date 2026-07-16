.PHONY: up down test lint migrate frontend-test frontend-lint frontend-build

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

frontend-test:
	cd frontend && npm test

frontend-lint:
	cd frontend && npm run lint

frontend-build:
	cd frontend && npm run build
