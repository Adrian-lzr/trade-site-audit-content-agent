# Backend

Install the locked dependencies from the repository root with:

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r backend\requirements.lock
```

The project metadata and allowed dependency ranges are in `pyproject.toml`; `requirements.lock` contains the resolved runtime and `pytest` development versions. Alternatively, `python -m pip install -e './backend[dev]'` installs using the ranges in the project metadata.

Run the API and Worker as separate processes from the repository root. They must share the same `DATABASE_URL` (SQLite is the default). Wait for API migrations to finish and its health check (`Invoke-RestMethod http://127.0.0.1:8000/health`) to succeed before starting the Worker; the Worker expects the API-created schema and does not run migrations. The root `Makefile` exposes the same commands as `make api`, `make worker`, `make db-migrate`, and `make health`.

```powershell
uvicorn backend.app:app --reload --port 8000
```

In a second terminal:

```powershell
python -m backend.worker
```

The local fixture server does not read `ALLOW_LOOPBACK`. Explicitly enable loopback access in the API and Worker environments, and register a literal loopback IP origin with `is_synthetic: true`. Start the fixture in another terminal:

```powershell
python -m backend.fixture_server --port 8765
```

Register `http://127.0.0.1:8765` with `allowed_paths` such as `/` or `/missing-title.html`. The fixture is a local synthetic demo and is not a production service. See the root README for the full API, Worker, fixture, and web workflow.
