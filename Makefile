.PHONY: help install backend frontend test lint check fmt db-reset

help:
	@echo "Stylobate dev targets:"
	@echo "  make install     - install backend + frontend deps"
	@echo "  make backend     - run backend on :8000"
	@echo "  make frontend    - run frontend on :3000"
	@echo "  make test        - run backend + frontend tests"
	@echo "  make lint        - lint everything"
	@echo "  make fmt         - autoformat everything"
	@echo "  make check       - lint + typecheck + tests"

install:
	cd backend && uv sync
	cd frontend && npm install

backend:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q
	cd frontend && npm test --silent

lint:
	cd backend && uv run ruff check . && uv run mypy app
	cd frontend && npm run lint

fmt:
	cd backend && uv run ruff format .
	cd frontend && npm run fmt

check: lint test
