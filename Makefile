.PHONY: install run dev test lint format verify icons clean

UV ?= uv
PORT ?= 8765

install:
	$(UV) sync --python 3.12

run:
	$(UV) run python -m server

# Run without touching the desktop: tools log their argv instead of executing.
dev:
	VOLCHINO_DRY_RUN=1 $(UV) run python -m server

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

# Live end-to-end check against a running server (AUTH_TOKEN must be in the environment).
verify:
	$(UV) run python scripts/ws_client.py --url ws://127.0.0.1:$(PORT)/ws --expect-zero-tokens \
		"volume 30%" "open firefox" "what's my work time today"

icons:
	$(UV) run python scripts/gen_icons.py

clean:
	rm -rf .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
