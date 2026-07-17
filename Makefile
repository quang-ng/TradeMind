.PHONY: up up-public deploy down test lint migrate frontend-test frontend-lint frontend-build

up:
	docker compose up -d --build

up-public:
	docker compose -f docker-compose.yml -f docker-compose.production.yml -f docker-compose.public.yml up -d --build

deploy:
	./scripts/deploy.sh

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
