# Optimization Status

Updated: 2026-10-06 (Asia/Shanghai)

Task status values are `not_started`, `in_progress`, `code_done`,
`fixture_verified`, `real_verified`, `blocked_external`, and `failed`.
Fixture or protocol evidence is never reported as real business evidence.

| task_id | status | commit | checks | evidence | blocker | next_action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | fixture_verified | 868c91b | baseline, synthetic holdout manifest, regression suite | `BASELINE.md`, `evals/optimization_t15_manifest.py` | real holdout and PostgreSQL target not supplied | keep synthetic and real evidence separate |
| T01 | fixture_verified | b1b8f5a | fact lifecycle, UTC and SQLite FK regression tests; isolated PostgreSQL migration/runtime evidence recorded under T17 | `backend/tests/test_optimization_t01.py`, `backend/services/facts.py`, `output/optimization/T17/evidence.json` | approved pilot fact set and production PostgreSQL target not supplied; isolated database remains synthetic | run the fact lifecycle against approved pilot facts on the authorized PostgreSQL target |
| T02 | fixture_verified | d74267f | document parser/frozen fragment tests plus 10 public GET parser source samples (metadata/hash only) | `backend/tests/test_document_parser.py`, `backend/document_parser.py`, `output/online-audit/zoogo-source-samples-20261005.json` | public page text is unverified and cannot become enterprise facts; no authorized full-site source set | use approved representative source materials when supplied |
| T03 | fixture_verified | 868c91b | current-fact resolution and stale binding tests | `backend/tests/test_optimization_t03.py`, `backend/services/facts.py` | live business data not supplied | validate with approved pilot facts |
| T04 | fixture_verified | 868c91b | typed claim and high-risk validation tests | `backend/tests/test_claim_validation.py`, `backend/services/claims.py` | no approved pilot fact set | run end-to-end with real source facts |
| T05 | fixture_verified | 868c91b | independent mention/citation/coverage metric tests | `backend/tests/test_visibility_metrics.py`, `backend/visibility_metrics.py` | real Provider not configured | execute a real cohort after Provider authorization |
| T06 | fixture_verified | d4dc1b4 | API contract, workspace-scope, creator-membership and production identity guard tests | `backend/tests/test_t06_contracts.py`, `backend/tests/test_authz.py`, `backend/schemas.py` | production identity is external | validate contract against deployed API |
| T07 | fixture_verified | d4dc1b4 | model usage, cost, unknown-outcome reconciliation and workspace budget-lock tests | `backend/tests/test_model_accounting.py`, `backend/services/accounting.py` | real model pricing/provider not configured | reconcile a real provider ledger |
| T08 | fixture_verified | d4dc1b4 | graph resume, approval idempotency, restart/rejection and fair queue polling tests | `backend/review_worker.py`, `backend/worker.py`, `backend/tests/test_content_worker_recovery.py`, `backend/tests/test_worker_dependency_isolation.py` | external publishing remains unconfigured | run worker recovery with staging outbox |
| T09 | fixture_verified | 868c91b | signed JWT issuer/audience/expiry/JWKS protocol tests | `backend/tests/test_identity.py`, `backend/identity.py` | real OIDC issuer/audience/JWKS not supplied | configure and verify the production issuer |
| T10 | fixture_verified | d74267f | CSV/PDF ingestion, source locator and hash tests; public HTML parser sample retained as read-only metadata/hash evidence | `backend/tests/test_ingestion.py`, `backend/ingestion.py`, `output/online-audit/zoogo-source-samples-20261005.json` | approved business CSV/PDF documents not supplied; public page text is not confirmed business data | ingest the pilot document set |
| T11 | fixture_verified | b1b8f5a | local API/Vite/Worker synthetic content journey via Playwright; build passed; task reached needs_more_information with fact/snapshot evidence | `output/playwright/t11-local-content.png`, `output/playwright/t11-local-content-task-needs-data.png`, `output/playwright/t11-seed.json`, `output/optimization/T11/evidence.json` | real OIDC/Membership, approved business documents and real Provider not supplied | repeat with authorized real identity and pilot data |
| T12 | fixture_verified | 7badac0 | constrained static HTML publisher and preservation tests; five-page local publication/outbox/idempotency rehearsal passed | `backend/tests/test_static_html_publisher.py`, `backend/publishers/static_html.py`, `output/optimization/T12/local_readiness-20261005.json`, `output/optimization/O04/local-html-publication-e2e-20261006.json` | local rehearsal intentionally has no remote target; fresh page verification and rollback remain unavailable without authorized staging | apply an approved revision to the authorized staging target and collect fresh page/rollback evidence |
| T13 | fixture_verified | 3821850 | signed callback, nonce replay protection, target/revision/commit binding, fresh observer fail-closed tests; local five-page callback/verification-unavailable rehearsal passed | `backend/services/deployment.py`, `backend/app.py`, `backend/schemas.py`, `backend/tests/test_deployment_verification.py`, `output/optimization/T13/evidence.json`, `output/optimization/T12/local_readiness-20261005.json` | no authorized staging callback, target observer, deployment target, or rollback environment | connect approved staging adapter and collect real callback/fetch/rollback evidence |
| T14 | blocked_external | 868c91b | adapter and metric fixtures; public GET source audit confirms no unauthenticated source can provide authorized Provider evidence | `INPUTS.md`, `backend/visibility_provider.py`, `output/optimization/T14-T15-T18/public-readonly-source-audit-20261005.json` | authorized Provider, consumer surface and budget not supplied | run the 30-sample real cohort |
| T15 | blocked_external | 868c91b | synthetic evaluation manifest and annotation tooling; public GET source audit remains parser-only and cannot replace holdout or human labels | `evals/optimization_t15_manifest.py`, `evals/tests`, `output/optimization/T14-T15-T18/public-readonly-source-audit-20261005.json` | real 30-case holdout and independent reviewers not supplied | freeze and run an independently reviewed holdout |
| T16 | blocked_external | 1fa03ee | API and Worker lifecycle audit events cover business and Worker transitions, publication rollback, hashed leases, type-only errors, audit payload redaction, selector/child-table fail-closed correlation, and request-only audited ID chain reconstruction; local and isolated PostgreSQL reports retain no business claim | `backend/observability.py`, `backend/worker_audit.py`, `backend/app.py`, `backend/tests/test_worker_audit.py`, `backend/tests/test_observability.py`, `backend/tests/test_authz.py`, `scripts/correlation_report.py`, `output/optimization/T16/correlation_report.json`, `output/optimization/T16/postgres_correlation_report.json`, `output/optimization/T16/evidence.json` | no real request/workflow rows in an authorized production runtime; fresh isolated PostgreSQL report remains empty by design | collect a bounded authorized real workflow run and regenerate report |
| T17 | fixture_verified | b068f52 | fresh PostgreSQL 16 migration, API/checkpoint/concurrency smoke, tmpfs backup/restore, local API/JobWorker rehearsal, mirror-based API/Worker/Web image build, and isolated full Compose API/Worker/Web/Fixture consumption passed; 251 backend tests and 10 eval tests pass | `output/optimization/T17/evidence.json`, `output/optimization/T17/docker-build-success-20261005173707.json`, `output/optimization/T17/docker-build-success-20261005173707.log`, `output/optimization/T17/compose-runtime-consumption-202610051751.json`, `output/optimization/T17/compose-ps-202610051751.json`, `output/optimization/T17/backup-restore-20261005134014b.json`, `output/optimization/T17/worker-postgres-rehearsal-2026100514.json`, `scripts/postgres_smoke.py`, `scripts/postgres_concurrency_smoke.py`, `scripts/local_postgres_restore.py`, `docs/phase-6-report.md` | production backup retention/recovery policy, production identity, and approved immutable production image provenance remain external; fixture runtime is not production evidence | run the same gates in the authorized production-like environment and verify policy/identity/image provenance |
| T18 | blocked_external | 868c91b | cohort-safe fixture reporting; public GET audit confirms only API documentation/protocol metadata is available | `INPUTS.md`, `output/optimization/T14-T15-T18/public-readonly-source-audit-20261005.json` | analytics/CRM source and 28-day observation window not supplied | configure the approved business cohort and window |
| T19 | blocked_external | d4dc1b4 | status/evidence pack includes T11/T13/T16/T17 runtime artifacts plus 2026-10-05 live read-only audits of both supplied sites and a parser source sample | `docs/optimization/STATUS.md`, `docs/optimization/INPUTS.md`, `output/optimization/`, `output/online-audit/zoogo-sites-20261005.json`, `output/online-audit/zoogo-sites-20261005-page15.json`, `output/online-audit/zoogo-source-samples-20261005.json` | T14/T15/T18 and real identity/site write/deployment inputs remain open; read-only reachability does not authorize release | complete authorized M2 inputs, then finalize O/C release matrix |

## Milestones

| milestone | status | evidence |
| --- | --- | --- |
| M0 | fixture_verified | T00-T05 have reproducible code/fixture evidence |
| M1 | blocked_external | T06-T13 and T16 code/fixture evidence; T11 fixture journey, T17 image build, and isolated Compose runtime gates pass locally, while real Provider/identity/site/review inputs remain open |
| M2 | blocked_external | real identity, authorized site, Provider, deployment and independent review inputs are absent |
| M3 | blocked_external | requires M2 plus a separately measured business observation window |

## Goal Matrix

| goal | status | evidence | next_action |
| --- | --- | --- | --- |
| O01 | fixture_verified | claim binding, current-fact resolution and review recovery tests | validate against approved pilot facts and PostgreSQL |
| O02 | fixture_verified | parser, snapshot metadata and rule evidence tests | add live representative pages when authorized |
| O03 | fixture_verified | LangGraph resume and review outbox recovery tests | exercise with staging worker runtime |
| O04 | fixture_verified | constrained HTML adapter plus local fresh observer, signed callback, verified state, manual-edit rollback conflict, successful guarded rollback and replay idempotency | connect authorized staging apply, callback, fresh fetch and rollback |
| O05 | fixture_verified | separated visibility metric definitions and fixtures | run authorized real Provider cohort |
| O06 | fixture_verified | CSV/PDF ingestion, source hash and locator tests | ingest approved business materials and measure retrieval |
| O07 | blocked_external | JWT protocol tests; local actor is not production identity | configure real OIDC and complete login/workspace role journey |
| O08 | fixture_verified | unified ModelCall ledger for content and visibility, unknown-cost retention, replay fencing, lease recovery, queue fairness and local idempotency evidence | reconcile a real provider ledger and run production-like concurrency/throughput checks |
| O09 | blocked_external | synthetic manifest only; zero independent human cases | run blinded 30-case holdout with independent reviewers |
| O10 | in_progress | 251 backend tests, 10 eval tests, Web build, PostgreSQL head/smoke/concurrency, isolated tmpfs backup/restore, source and Compose gates pass | verify approved production image provenance, production identity, backup retention/recovery policy, and authorized runtime |

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
