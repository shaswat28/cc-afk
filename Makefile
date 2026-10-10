.PHONY: check lint test install-hooks

check: lint test

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

install-hooks:
	git config core.hooksPath .githooks
	@echo "git hooks enabled from .githooks/"
