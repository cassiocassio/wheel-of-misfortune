# Wheel of Misfortune — common tasks. `make help` lists them.
# Single worker only (SPEC §3): shared state is an in-process-guarded JSON file.

VENV := .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.DEFAULT_GOAL := help
.PHONY: help venv install test lint run dev icons deploy undeploy clean

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-10s\033[0m %s\n", $$1, $$2}'

venv: ## Create the virtualenv
	python3 -m venv $(VENV)

install: venv ## Install runtime + dev dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]" 2>/dev/null || $(PIP) install pytest ruff

test: ## Run the test suite
	$(PY) -m pytest

lint: ## Lint with ruff
	$(VENV)/bin/ruff check app tests deploy

run: ## Serve on the LAN, port 8000 (foreground; serves while this host is awake)
	$(VENV)/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

dev: ## Serve locally for development (127.0.0.1:8000, autoreload)
	$(VENV)/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

icons: ## Regenerate the PWA icons (stdlib only)
	$(PY) deploy/make_icons.py

deploy: ## Install + start the background service (launchd on macOS, systemd on Linux)
	./deploy/install.sh

undeploy: ## Stop + remove the background service
	./deploy/install.sh uninstall

clean: ## Remove caches + bytecode
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -not -path './$(VENV)/*' -exec rm -rf {} + 2>/dev/null || true
