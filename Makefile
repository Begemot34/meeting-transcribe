# Checks for development; ./transcribe does not use this file.
# The check environment is .venv-check with only the "test" dependency group: the app's .venv stays as setup made it.
export UV_PROJECT_ENVIRONMENT := .venv-check
UV_RUN := uv run --locked --exact --only-group test

.PHONY: check lint test

check: lint test

lint:
	$(UV_RUN) ruff check
	shellcheck -S warning transcribe
	bash -n transcribe

test:
	$(UV_RUN) pytest
