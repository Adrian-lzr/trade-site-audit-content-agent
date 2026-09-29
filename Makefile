.PHONY: api worker fixture web test web-build db-migrate db-up db-down db-logs health

# The commands use the currently activated Python environment.  On Windows,
# run them from a shell where the repository venv is activated first.
PYTHON ?= python

api:
	$(PYTHON) -m uvicorn backend.app:app --reload --port 8000

worker:
	$(PYTHON) -m backend.worker

fixture:
	$(PYTHON) -m backend.fixture_server --port 8765

web:
	npm --prefix apps/web run dev

test:
	$(PYTHON) -m pytest backend/tests -q

web-build:
	npm --prefix apps/web run build

db-migrate:
	$(PYTHON) -m alembic -c backend/alembic.ini upgrade head

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-logs:
	docker compose logs -f postgres

health:
	$(PYTHON) -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
