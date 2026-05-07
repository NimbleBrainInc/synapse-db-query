.PHONY: dev build build-ui bundle clean clean-bundle check lint typecheck test run run-http

# Development — Vite with HMR preview (server runs from src/, no deps snapshot)
dev:
	cd ui && npm run dev

# Build — UI only (faster than `bundle`, skips deps refresh)
build: build-ui

build-ui:
	cd ui && npm install && npm run build

# Bundle — full build for the path-install path used by mpak / NimbleBrain.
# Refreshes ui/dist/ AND deps/ (the snapshot Python imports from).
# Run this after any src/ or ui/ change before retesting under NimbleBrain,
# then restart the runtime so the bundle subprocess respawns.
bundle: build-ui clean-bundle
	@uv pip install --target ./deps --only-binary :all: . 2>/dev/null || uv pip install --target ./deps .

clean-bundle:
	rm -rf deps/

# Run server in stdio mode (uses src/ directly)
run:
	uv run python -m synapse_db_query.server

# Run server in HTTP mode
run-http:
	uv run uvicorn synapse_db_query.server:app --port 8001

# Verification — single quality gate. Run before commits and after `bundle`.
check: lint test

lint:
	uv run ruff check src/

typecheck:
	uv run ty check src/

test:
	uv run pytest tests/

clean: clean-bundle
	rm -rf ui/dist ui/node_modules

# Version bump — updates manifest.json, server.json, pyproject.toml, __init__.py
bump:
ifndef VERSION
	$(error VERSION is required. Usage: make bump VERSION=0.2.0)
endif
	@echo "Bumping to $(VERSION)"
	@jq --arg v "$(VERSION)" '.version = $$v' manifest.json > manifest.tmp.json && mv manifest.tmp.json manifest.json
	@jq --arg v "$(VERSION)" '.version = $$v' server.json > server.tmp.json && mv server.tmp.json server.json
	@sed -i '' 's/^version = .*/version = "$(VERSION)"/' pyproject.toml
	@sed -i '' 's/^__version__ = .*/__version__ = "$(VERSION)"/' src/synapse_db_query/__init__.py
	@echo "Done. Don't forget to commit and tag."
