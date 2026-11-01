.PHONY: sync dev test lint fmt db-up db-down docker-build

sync:
	uv sync --python 3.12

dev:
	uv run uvicorn act_agent.main:app --host 0.0.0.0 --port 8088 --reload

run:
	uv run uvicorn act_agent.main:app --host 0.0.0.0 --port 8088

cli:
	uv run python -m act_agent.cli

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

fmt:
	uv run ruff check --fix src tests

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

docker-build:
	docker compose build
