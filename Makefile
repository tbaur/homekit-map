# Build and install stay inside this checkout. .venv is gitignored.
# Run `make help` to list targets.

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
VENV := $(ROOT)/.venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.DEFAULT_GOAL := install

.PHONY: help install build test lint check clean

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

install: ## Create .venv and install the tool with dev extras
	@if [ ! -x $(PYTHON) ] || ! $(PYTHON) -c 'import sys' >/dev/null 2>&1; then rm -rf $(VENV); fi
	@$(MAKE) $(VENV)/bin/homekit-map

build: install

$(VENV)/bin/python:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools

$(VENV)/bin/homekit-map: $(VENV)/bin/python pyproject.toml
	$(PIP) install -e "$(ROOT)[dev]"
	@touch $@

test: install ## pytest with the coverage gate in pyproject.toml
	$(PYTHON) -m pytest --cov --cov-report=term

lint: install ## Ruff
	$(VENV)/bin/ruff check src tests

check: lint test ## Everything CI runs except pip-audit

clean: ## Remove .venv, build output, and caches
	rm -rf $(VENV) $(ROOT)/build $(ROOT)/dist $(ROOT)/src/*.egg-info $(ROOT)/*.egg-info
	rm -rf $(ROOT)/.pytest_cache $(ROOT)/.ruff_cache $(ROOT)/.coverage
