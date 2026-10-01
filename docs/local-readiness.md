# Local Readiness Rehearsal

This document records the checks that can be reproduced on a developer machine
without contacting a customer site, remote Git, CMS, or a real deployment
system. The local publication flow is an isolation rehearsal. A successful
result does not mean that a production deployment or a public page changed.

## Prerequisites

- Python 3.12 with the locked backend environment (`backend/.venv` in the
  commands below).
- Git on `PATH` for the five-page rehearsal.
- Docker Compose for the static Compose check.
- `pg_dump` and `psql` on `PATH`, or two explicitly supplied temporary Docker
  containers that provide those tools, for the PostgreSQL backup/restore check.
- A separate local PostgreSQL source database and an empty database whose name
  ends in `_restore` for the restore check. The script never drops a database
  or removes a Docker volume.

## Five-Page Publication Rehearsal

The harness creates five synthetic page snapshots in a temporary SQLite
database, creates one approved changeset per page, and submits each to a new
local Git repository. It then records five local deployment callbacks, verifies
the Git objects, and rolls back the first deployment with an expected-SHA
compare-and-swap. Duplicate publication and rollback requests are checked for
idempotency.

Use a new directory when the artifacts should remain available for inspection:

```powershell
$py = (Resolve-Path "backend/.venv/Scripts/python.exe")
$run = Join-Path (Get-Location) "output/local-readiness-$(Get-Date -Format yyyyMMdd-HHmmss)"
& $py scripts/local_readiness.py --workdir $run --output (Join-Path $run "report.json")
```

The JSON report must contain:

```text
status = passed
pages = 5
published_attempts = 5
deployment_callbacks = 5
verified_deployments = 5
rollback.source_final_status = rolled_back
rollback.rollback_final_status = verified
idempotency.publication_replay_rejected = true
idempotency.publication_outbox_unique = true
idempotency.rollback_replay_same_attempt = true
external_side_effects.remote_git_push = false
external_side_effects.cms_write = false
external_side_effects.external_deployment = false
```

`--workdir` must be empty or new. The default (without `--workdir`) uses a
temporary directory and removes it after closing the SQLAlchemy engine. This
path is covered by a Windows process-level regression test.

Run the focused test directly:

```powershell
& $py -m pytest -p no:cacheprovider backend/tests/test_local_readiness.py -q
```

The test proves the local state machine and cleanup. It does not prove remote
PR creation, CMS writes, deployment orchestration, or live HTTP content.

## Docker Compose Boundary Check

Render both Compose profiles without starting containers or building images:

```powershell
$env:POSTGRES_PASSWORD = "local-readiness-config-only"
& $py scripts/local_compose_check.py
```

The check verifies that:

- `postgres`, `api`, `worker`, and `web` exist in the default profile.
- `fixture` is present only in the `demo` profile and shares the API network
  namespace without an independent host port.
- Host ports are bound to `127.0.0.1`.
- Worker startup waits for a healthy API, and API startup runs Alembic to head.

This is a rendered-configuration check. It intentionally reports
`containers_started=false` and `images_built=false`. To separately exercise
the local runtime after reviewing the rendered config:

```powershell
docker compose -p trade-visibility-readiness up --build -d
Invoke-RestMethod http://127.0.0.1:8000/health
docker compose -p trade-visibility-readiness down
```

Use a project name and ports that are not shared with another running stack.

## PostgreSQL Backup and Restore

The script requires a local source DSN and a separately created empty target
database. It refuses remote hosts, equal source/target databases, non-empty
targets, and target names that do not end in `_restore`.

```powershell
$py = (Resolve-Path "backend/.venv/Scripts/python.exe")
$source = "postgresql+psycopg://trade_visibility:<password>@127.0.0.1:5434/trade_visibility"
$target = "postgresql+psycopg://trade_visibility:<password>@127.0.0.1:5434/trade_visibility_restore"
$backup = Join-Path (Get-Location) "output/local-readiness/backup.sql"
& $py scripts/local_postgres_restore.py `
  --source-database-url $source `
  --target-database-url $target `
  --backup $backup
```

The source must already be migrated to the repository head. A passing report
includes the Alembic head (`0014_memberships_audit_events_snapshot_metadata` in
the current tree), the SHA-256 and byte size of the dump, and equal row counts
for `workspaces`, `sites`, `page_snapshots`, and `visibility_runs`. Restore is
run with `psql --single-transaction -v ON_ERROR_STOP=1` and the target is not
dropped on failure, so an operator can inspect it before deciding what to do.

When the host does not have PostgreSQL client binaries, pass
`--source-container` and `--target-container` with simple Docker container
names. The source and target containers must expose their databases on
localhost for the SQLAlchemy state checks and must use only a tmpfs mount at
`/var/lib/postgresql/data`; named and anonymous Docker data volumes are
rejected. The script invokes `docker exec` inside those containers and streams
the dump over stdin; it does not mount a host path or remove either container.
For this repository's rehearsal, create the target database in a temporary
tmpfs-backed container and remove that container only after reviewing the
result:

```powershell
& $py scripts/local_postgres_restore.py `
  --source-database-url $source `
  --target-database-url $target `
  --backup $backup `
  --source-container <temporary-source-container> `
  --target-container <temporary-target-container>
```

Do not pass the existing Compose `trade-visibility-postgres-1` container as a
source or target. Both must be separately created tmpfs-backed containers and
databases.

The dump can contain HTML, facts, and audit evidence. Keep it outside Git and
protect it as production data. Retention, encryption, point-in-time recovery,
and restore-time objectives remain deployment responsibilities.

## Evidence Boundaries

Record the JSON output and command date for each check. The following claims
are valid from this document:

- Five-page diffs and the local Git commit/revert state machine are reproducible
  with synthetic data.
- Compose profile and network exposure rules are statically validated.
- PostgreSQL backup/restore is accepted only after the explicit source/target
  command completes successfully.

The checks do not establish natural-search ranking, AI citations, customer
traffic, remote PR behavior, production PostgreSQL concurrency, or a live
deployment callback. Those require separately authorized environments and
evidence.

## Observed Local Run (2026-10-01)

The following evidence was produced in this workspace, rather than inferred
from configuration:

- `scripts/local_readiness.py`: 5 pages, 5 submitted local publication
  attempts, 5 verified deployment callbacks, and one verified rollback. The
  report marked remote Git push, CMS write, and external deployment as false.
- `scripts/local_compose_check.py`: default and `demo` profiles passed; the
  report marked container start and image build as false.
- `scripts/local_postgres_restore.py`: two temporary `postgres:16-alpine`
  containers with tmpfs-only data mounts (no persistent Docker volumes);
  source and target were migrated/created on
  localhost ports, and the target database was empty before restore. The
  report recorded head `0014_memberships_audit_events_snapshot_metadata`, row
  counts `workspaces=1`, `sites=1`, `page_snapshots=1`,
  `visibility_runs=1`, a 66,105-byte dump, and SHA-256
  `899827d8371b3914187894431489f197158630e428153b9d432db720ad1b7f6a`.
  Both temporary containers were removed afterwards; the existing
  `trade-visibility-postgres-1` named volume was not used.

The PostgreSQL numbers above are a synthetic restore rehearsal. They do not
represent production backup retention, encryption, concurrency, or recovery
time objectives.

## PostgreSQL Concurrency Smoke

`scripts/postgres_concurrency_smoke.py` runs a focused concurrency check against
an empty PostgreSQL database bound to localhost. It refuses non-local hosts and
databases containing application rows. The check races two visibility budget
reservations against a single run, races two publication workers for one outbox
event, and verifies that expired outbox and visibility leases can be recovered
and claimed again. It cleans up the rows it creates; the Alembic schema remains
at head.

```powershell
$databaseUrl = "postgresql+psycopg://<user>:<password>@127.0.0.1:<port>/<empty_test_database>"
& (Resolve-Path "backend/.venv/Scripts/python.exe") `
  scripts/postgres_concurrency_smoke.py --database-url $databaseUrl
```

This simulates lease expiry after a worker interruption; it does not kill and
restart a worker process, measure production load, or validate production
retention, encryption, PITR, or recovery-time objectives.
