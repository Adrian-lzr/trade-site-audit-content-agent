# Optimization Status

Updated: 2026-10-05 (Asia/Shanghai)

Task status values are `not_started`, `in_progress`, `code_done`,
`fixture_verified`, `real_verified`, `blocked_external`, and `failed`.
Fixture or protocol evidence is never reported as real business evidence.

| task_id | status | commit | checks | evidence | blocker | next_action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | fixture_verified | 868c91b | baseline, synthetic holdout manifest, regression suite | `BASELINE.md`, `evals/optimization_t15_manifest.py` | real holdout and PostgreSQL target not supplied | keep synthetic and real evidence separate |
| T01 | fixture_verified | 868c91b | fact lifecycle, UTC and SQLite FK regression tests | `backend/tests/test_optimization_t01.py`, `backend/services/facts.py` | PostgreSQL integration target not supplied | run the same migration/tests against isolated PostgreSQL |
| T02 | fixture_verified | 868c91b | document parser and frozen fragment tests | `backend/tests/test_document_parser.py`, `backend/document_parser.py` | none for fixture path | add representative live source samples when supplied |
| T03 | fixture_verified | 868c91b | current-fact resolution and stale binding tests | `backend/tests/test_optimization_t03.py`, `backend/services/facts.py` | live business data not supplied | validate with approved pilot facts |
| T04 | fixture_verified | 868c91b | typed claim and high-risk validation tests | `backend/tests/test_claim_validation.py`, `backend/services/claims.py` | no approved pilot fact set | run end-to-end with real source facts |
| T05 | fixture_verified | 868c91b | independent mention/citation/coverage metric tests | `backend/tests/test_visibility_metrics.py`, `backend/visibility_metrics.py` | real Provider not configured | execute a real cohort after Provider authorization |
| T06 | fixture_verified | 868c91b | API contract and workspace-scope tests | `backend/tests/test_t06_contracts.py`, `backend/schemas.py` | production identity is external | validate contract against deployed API |
| T07 | fixture_verified | 868c91b | model usage, cost and unknown-outcome tests | `backend/tests/test_model_accounting.py`, `backend/model_accounting.py` | real model pricing/provider not configured | reconcile a real provider ledger |
| T08 | fixture_verified | 868c91b | graph resume, approval idempotency, restart and rejection tests | `backend/review_worker.py`, `backend/tests/test_content_worker_recovery.py` | external publishing remains unconfigured | run worker recovery with staging outbox |
| T09 | fixture_verified | 868c91b | signed JWT issuer/audience/expiry/JWKS protocol tests | `backend/tests/test_identity.py`, `backend/identity.py` | real OIDC issuer/audience/JWKS not supplied | configure and verify the production issuer |
| T10 | fixture_verified | 868c91b | CSV/PDF ingestion, source locator and hash tests | `backend/tests/test_ingestion.py`, `backend/ingestion.py` | approved business documents not supplied | ingest the pilot document set |
| T11 | fixture_verified | b1b8f5a | local API/Vite/Worker synthetic content journey via Playwright; build passed; task reached needs_more_information with fact/snapshot evidence | `output/playwright/t11-local-content.png`, `output/playwright/t11-local-content-task-needs-data.png`, `output/playwright/t11-seed.json`, `output/optimization/T11/evidence.json` | real OIDC/Membership, approved business documents and real Provider not supplied | repeat with authorized real identity and pilot data |
| T12 | fixture_verified | 868c91b | constrained static HTML publisher and preservation tests | `backend/tests/test_static_html_publisher.py`, `backend/static_html_publisher.py` | no authorized staging repository supplied | apply an approved revision to staging |
| T13 | fixture_verified | 3821850 | signed callback, nonce replay protection, target/revision/commit binding, fresh observer fail-closed tests | `backend/services/deployment.py`, `backend/app.py`, `backend/schemas.py`, `backend/tests/test_deployment_verification.py`, `output/optimization/T13/evidence.json` | no authorized staging callback, target observer, deployment target, or rollback environment | connect approved staging adapter and collect real callback/fetch/rollback evidence |
| T14 | blocked_external | 868c91b | adapter and metric fixtures only | `INPUTS.md`, `backend/visibility_provider.py` | authorized Provider, consumer surface and budget not supplied | run the 30-sample real cohort |
| T15 | blocked_external | 868c91b | synthetic evaluation manifest and annotation tooling | `evals/optimization_t15_manifest.py`, `evals/tests` | real 30-case holdout and independent reviewers not supplied | freeze and run an independently reviewed holdout |
| T16 | blocked_external | b1b8f5a | bounded PostgreSQL correlation report, redaction, selector scoping and artifact hash; no matching runtime rows | `backend/observability.py`, `scripts/correlation_report.py`, `output/optimization/T16/postgres_correlation_report.json`, `output/optimization/T16/evidence.json` | no real request/workflow rows in the migrated validation database | collect a bounded real workflow run and regenerate report |
| T17 | blocked_external | b1b8f5a | fresh PostgreSQL 16 migration, API/checkpoint smoke, concurrency/lease smoke, source and Compose gates passed; 236 backend tests and 10 eval tests pass | `output/optimization/T17/evidence.json`, `scripts/postgres_smoke.py`, `scripts/postgres_concurrency_smoke.py`, `docs/phase-6-report.md` | Docker Hub OAuth timeout blocked image build; production backup/recovery and worker consumption remain unverified | rerun image build with registry access and execute full Compose/backup gates |
| T18 | blocked_external | 868c91b | cohort-safe fixture reporting only | `INPUTS.md` | analytics/CRM source and 28-day observation window not supplied | configure the approved business cohort and window |
| T19 | blocked_external | b1b8f5a | status/evidence pack includes T11/T13/T16/T17 runtime artifacts plus 2026-10-05 live read-only audits of both supplied sites | `docs/optimization/STATUS.md`, `docs/optimization/INPUTS.md`, `output/optimization/`, `output/online-audit/zoogo-sites-20261005.json` | T14/T15/T18 and real identity/site write/deployment inputs remain open; read-only reachability does not authorize release | complete authorized M2 inputs, then finalize O/C release matrix |

## Milestones

| milestone | status | evidence |
| --- | --- | --- |
| M0 | fixture_verified | T00-T05 have reproducible code/fixture evidence |
| M1 | blocked_external | T06-T13 and T16 code/fixture evidence; T11 fixture journey and PostgreSQL runtime gates now pass locally, but Docker image build and remaining runtime gates are open |
| M2 | blocked_external | real identity, authorized site, Provider, deployment and independent review inputs are absent |
| M3 | blocked_external | requires M2 plus a separately measured business observation window |

## Goal Matrix

| goal | status | evidence | next_action |
| --- | --- | --- | --- |
| O01 | fixture_verified | claim binding, current-fact resolution and review recovery tests | validate against approved pilot facts and PostgreSQL |
| O02 | fixture_verified | parser, snapshot metadata and rule evidence tests | add live representative pages when authorized |
| O03 | fixture_verified | LangGraph resume and review outbox recovery tests | exercise with staging worker runtime |
| O04 | in_progress | constrained HTML publisher plus signed callback and fresh target verification adapter | connect authorized staging apply, callback, fresh fetch and rollback |
| O05 | fixture_verified | separated visibility metric definitions and fixtures | run authorized real Provider cohort |
| O06 | fixture_verified | CSV/PDF ingestion, source hash and locator tests | ingest approved business materials and measure retrieval |
| O07 | blocked_external | JWT protocol tests; local actor is not production identity | configure real OIDC and complete login/workspace role journey |
| O08 | in_progress | model ledger and worker recovery paths | finish fairness/cost reconciliation under real runtime |
| O09 | blocked_external | synthetic manifest only; zero independent human cases | run blinded 30-case holdout with independent reviewers |
| O10 | in_progress | 236 backend tests, 10 eval tests, Web build, PostgreSQL head/smoke/concurrency, source and Compose gates pass | complete Docker image build, backup/recovery, worker consumption and real identity checks |

## Critical Cases

| case_id | status | evidence | next_action |
| --- | --- | --- | --- |
| C01-C05 | fixture_verified | claim/fact lifecycle regression tests | rerun on pilot facts and PostgreSQL |
| C06 | fixture_verified | shared document parser regression set | add authorized live fragments |
| C07 | fixture_verified | independent visibility metric cases | execute real Provider samples |
| C08-C11 | fixture_verified | review resume, lease fencing, idempotency and restart tests | validate with staging worker/outbox |
| C12 | fixture_verified | signed-token and workspace authorization fixtures | configure real issuer and memberships |
| C13-C14 | fixture_verified | constrained publisher, signed deployment callback, nonce replay and fresh observer unit tests | perform authorized staging page apply/fetch/rollback |
| C15 | blocked_external | synthetic fixtures only; no independent human evaluation | obtain holdout and reviewers |
| C16 | blocked_external | source readiness checks only | run full container and PostgreSQL runtime |

## Evidence boundary

No production claim is made from fixtures, synthetic samples, local Git commits,
or protocol-only identity tests. Real OIDC, Provider, staging/production write
scope, PostgreSQL/Docker runtime, approved business documents, independent
reviewers, and analytics/CRM data remain explicit external inputs in `INPUTS.md`.
