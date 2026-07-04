.PHONY: help install dev test lint format typecheck run clean docker-build docker-up docker-down

help:
	@echo "Telebot - Telegram Remote Controller for OpenCode"
	@echo ""
	@echo "Commands:"
	@echo "  install      Install production dependencies"
	@echo "  dev          Install development dependencies"
	@echo "  test         Run tests"
	@echo "  lint         Run linting (ruff)"
	@echo "  format       Format code (black + ruff)"
	@echo "  typecheck    Run type checking (mypy)"
	@echo "  run          Run the bot"
	@echo "  clean        Clean build artifacts"
	@echo "  docker-build Build Docker image"
	@echo "  docker-up    Start with docker-compose"
	@echo "  docker-down  Stop docker-compose"

install:
	uv sync

dev:
	uv sync --extra dev

test:
	uv run pytest -v

lint:
	uv run ruff check app tests

format:
	uv run ruff format app tests
	uv run ruff check --fix app tests

typecheck:
	uv run mypy app

run:
	uv run python -m app.bot.main

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

docker-build:
	docker build -t telebot .

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

docker-logs:
	docker-compose logs -f