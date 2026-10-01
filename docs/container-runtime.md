# Container Runtime

The root Compose file runs the local stack with PostgreSQL, the API, the
background Worker, and the static Web application. The API applies the Alembic
`head` migration before it starts listening; its health check is the gate for
the Worker and Web services.

## Start the stack

Set a local database password before Compose interpolation. Do not use the
example value as a production secret:

```powershell
Copy-Item .env.example .env
# Edit .env and replace POSTGRES_PASSWORD with a local secret.
docker compose up --build -d
```

The services are bound to loopback by default:

- Web: `http://127.0.0.1:5173`
- API health: `http://127.0.0.1:8000/health`
- PostgreSQL: `127.0.0.1:5434` (override with `POSTGRES_PORT`)

The Web container serves the Vite build through nginx and proxies `/api/` to
the API service, so browser requests remain same-origin. The host port `5173`
maps to the unprivileged nginx listener on container port `8080`.

The backend image runs the API, Worker, and demo fixture as UID `10001`; the
Web image runs nginx as its `nginx` user. Both images contain a health check.
The API health check is also the Compose startup gate for Worker and Web.

## Reproducible build checks

Application dependencies are installed only from the committed lock files:
`backend/requirements.lock` and `apps/web/package-lock.json`. Run the source
gate before attempting a network build; it does not pull images or start
containers:

```powershell
backend\.venv\Scripts\python.exe scripts\container_build_check.py
```

The gate checks the Docker build contexts, lock-file install commands, nginx
listener, non-root users, image and Compose health checks, loopback port
mapping, Alembic startup migration, and health-gated service dependencies. A
passing result reports `images_built: false` by design.

When Docker Hub (or an approved registry mirror) is reachable, build the two
application images and record the command output and resulting image digests:

```powershell
$env:POSTGRES_PASSWORD = "local-only-validation-secret"
docker compose build api web
docker image inspect trade-visibility-api trade-visibility-web --format '{{.RepoDigests}}'
```

The base image tags are explicit (`python:3.12-slim`, `node:22-alpine`, and
`nginx:1.27-alpine`) but remain registry inputs; production deployments should
resolve and pin approved image digests. A registry authentication or network
failure is a failed build attempt, not evidence that an image was built.

## Demo fixture profile

The fixture is opt-in. It is not started by the default stack and has no public
network binding of its own. In the `demo` profile it shares the API network
namespace, binds `127.0.0.1:8765`, and is reachable only through the local API
and Worker containers (and the host loopback mapping):

```powershell
$env:ALLOW_LOOPBACK = "true"
docker compose --profile demo up --build -d
Invoke-RestMethod http://127.0.0.1:8000/health
```

`ALLOW_LOOPBACK=true` is deliberately explicit. Synthetic sites still must be
registered with a literal loopback origin such as
`http://127.0.0.1:8765` and `is_synthetic=true`; the crawler's same-origin,
allowed-path, DNS pinning, and public-address checks remain active.

## Stop and inspect

```powershell
docker compose ps
docker compose logs api worker web
docker compose down
```

`docker compose down` leaves the named PostgreSQL volume in place. Remove or
reset it only as an explicit local data-management action.

## Backup and restore rehearsal

Run a dump while writes are stopped or during an agreed maintenance window.
The plain SQL format is intentionally used here because it can be inspected
and streamed by PowerShell without requiring a second PostgreSQL client on the
host:

```powershell
$backupPath = Join-Path (Get-Location) "trade-visibility-backup.sql"
docker compose exec -T postgres sh -c 'pg_dump --format=plain --no-owner --no-acl -U "$POSTGRES_USER" -d "$POSTGRES_DB"' > $backupPath
Get-FileHash $backupPath -Algorithm SHA256
```

The dump may contain HTML, facts, and audit evidence. Store it outside Git and
protect it as production data. Restore only into an empty, isolated database or
after an approved maintenance window:

```powershell
Get-Content -Raw $backupPath | docker compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT version_num FROM alembic_version"'
```

The restore rehearsal acceptance check is: the command exits successfully,
`alembic_version` reports `0014_memberships_audit_events_snapshot_metadata`, and representative
row counts for `workspaces`, `sites`, `page_snapshots`, and `visibility_runs`
match the source database. This repository's PostgreSQL migration evidence uses
a separate no-volume temporary container; it does not touch the Compose named
volume. Production backup retention, encryption, point-in-time recovery, and
restore time objectives remain deployment responsibilities.

## Application and checkpoint smoke

After the migrations have completed, run the application and checkpoint smoke
against an isolated PostgreSQL database (for example, a disposable Compose
project/database). Do not point it at a production database or a shared
application volume: the script creates one uniquely named site and one
checkpoint thread and leaves those rows for inspection. It uses FastAPI's
`TestClient` in-process, so an API container does not need to be running, but
the database must be reachable and already migrated:

```powershell
# Replace <local-password> and the port/database with the isolated target.
$env:DATABASE_URL = "postgresql+psycopg://trade_visibility:<local-password>@127.0.0.1:5434/trade_visibility"
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
backend\.venv\Scripts\python.exe scripts\postgres_smoke.py --database-url $env:DATABASE_URL
```

The script rejects non-PostgreSQL DSNs. A successful run prints JSON containing
the migration head, both health statuses, the temporary site name, and
`checkpoint_reopen_count` equal to `1`. The smoke does not crawl a site or call
publication/CMS endpoints.

## Static validation

With Docker installed, validate interpolation and service dependencies without
starting containers:

```powershell
$env:POSTGRES_PASSWORD = "local-only-validation-secret"
docker compose config
backend\.venv\Scripts\python.exe scripts\container_build_check.py
```

The rendered configuration should contain `postgres`, `api`, `worker`, `web`,
and the profile-gated `fixture`; `worker` must depend on the healthy API, and
the fixture must retain `network_mode: service:api` with no independent public
port mapping. These checks do not build images. Use `docker compose build` only
when the registry is reachable, and retain its output as separate build
evidence.
