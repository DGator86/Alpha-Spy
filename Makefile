PYTHON ?= python3
VERSION := $(shell $(PYTHON) -c "import re,pathlib;print(re.search(r'^version\s*=\s*\"([^\"]+)\"',pathlib.Path('pyproject.toml').read_text(),re.M).group(1))")
NAME := alpha-spy-v$(VERSION)
# Build output. release/ holds the archived upstream drops and is not written to.
RELEASE_DIR := dist/release

.PHONY: help venv test lint smoke build release verify verify-release deploy clean

help:
	@echo "Alpha-SPY $(VERSION)"
	@echo
	@echo "  make venv            create .venv with runtime and dev dependencies"
	@echo "  make lint            static validation (python, shell, javascript, systemd)"
	@echo "  make test            run the test suite"
	@echo "  make build           build the application wheel into dist/"
	@echo "  make smoke           install the built wheel in a temp venv and exercise it"
	@echo "  make verify          lint + test + build + smoke"
	@echo "  make release         build $(RELEASE_DIR)/$(NAME).{tar.gz,zip} with checksums"
	@echo "  make verify-release  re-check the published archives against their checksums"
	@echo "  make deploy          deploy a release to a VPS over SSH (see DEPLOY_HOST)"
	@echo "  make clean           remove build output and caches"

venv:
	$(PYTHON) -m venv .venv
	.venv/bin/pip install --upgrade pip wheel
	.venv/bin/pip install -e ".[dev]"
	@echo "Activate with: source .venv/bin/activate"

test:
	PYTHONPATH=src:. pytest -q

lint:
	bash scripts/check_legacy_identifiers.sh
	$(PYTHON) -m compileall -q src tests examples
	bash -n install.sh scripts/*.sh scripts/alpha-spy-backup
	@if command -v ruff >/dev/null; then ruff check src tests examples; \
		else echo "ruff not installed (make venv); skipped lint"; fi
	@if command -v node >/dev/null; then node --check src/spy_alpha_dashboard/static/app.js; \
		else echo "node not installed; skipped dashboard javascript check"; fi
	bash scripts/verify_units.sh

build:
	$(PYTHON) -m build --wheel --outdir dist

smoke:
	bash scripts/smoke_test.sh

release:
	bash scripts/build_release.sh

verify: lint test build smoke

verify-release:
	@test -f "$(RELEASE_DIR)/$(NAME).tar.gz" || { echo "No release in $(RELEASE_DIR); run 'make release'"; exit 1; }
	cd $(RELEASE_DIR) && sha256sum -c $(NAME).tar.gz.sha256 $(NAME).zip.sha256
	bash scripts/verify_release.sh $(RELEASE_DIR)/$(NAME).tar.gz

deploy:
	bash scripts/deploy_vps.sh

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .ruff_cache
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
