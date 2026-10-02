# Optimization Status

Updated: 2026-10-02 (Asia/Shanghai)

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
| T11 | in_progress | 73657e8 | web build passed; empty-state Playwright screenshot | `apps/web/src/ContentReviewWorkspace.tsx`, `output/playwright/t11-content-empty.png` | API returned 502 and no real task/evidence data; OIDC/Membership UI not exercised | rerun the complete browser journey with a live API and authorized user |
| T12 | fixture_verified | 868c91b | constrained static HTML publisher and preservation tests | `backend/tests/test_static_html_publisher.py`, `backend/static_html_publisher.py` | no authorized staging repository supplied | apply an approved revision to staging |
| T13 | in_progress | 73657e8 | verifier policy unit tests; API returns `verification_unavailable` without a fresh target read; rollback stays pending | `backend/services/deployment.py`, `backend/app.py`, `backend/tests/test_deployment_verification.py`, `backend/tests/test_publication_worker.py` | no staging callback or page fetch configured | integrate fresh target read and deployment callback |
| T14 | blocked_external | 868c91b | adapter and metric fixtures only | `INPUTS.md`, `backend/visibility_provider.py` | authorized Provider, consumer surface and budget not supplied | run the 30-sample real cohort |
| T15 | blocked_external | 868c91b | synthetic evaluation manifest and annotation tooling | `evals/optimization_t15_manifest.py`, `evals/tests` | real 30-case holdout and independent reviewers not supplied | freeze and run an independently reviewed holdout |
| T16 | in_progress | 73657e8 | request/audit correlation paths added | `backend/observability.py`, `backend/app.py` | full cross-service event report not assembled | produce the audit/workflow/model/publication correlation report |
| T17 | blocked_external | 868c91b | source-level migration and readiness checks | `backend/tests/test_container_build_check.py`, `INPUTS.md` | Docker daemon and PostgreSQL runtime unavailable | run clean Compose and PostgreSQL recovery checks |
| T18 | blocked_external | 868c91b | cohort-safe fixture reporting only | `INPUTS.md` | analytics/CRM source and 28-day observation window not supplied | configure the approved business cohort and window |
| T19 | in_progress | 73657e8 | optimization artifacts and evidence references are present | `docs/optimization/STATUS.md`, `docs/optimization/PLAN.md` | depends on T11, T16 and external gates | finalize O/C matrices and runtime/evaluation pack |

## Milestones

| milestone | status | evidence |
| --- | --- | --- |
| M0 | fixture_verified | T00-T05 have reproducible code/fixture evidence |
| M1 | in_progress | T06-T13 and T16 have substantial code evidence; T11/T16/T19 remain open and T17 needs runtime verification |
| M2 | blocked_external | real identity, authorized site, Provider, deployment and independent review inputs are absent |
| M3 | blocked_external | requires M2 plus a separately measured business observation window |

## Goal Matrix

| goal | status | evidence | next_action |
| --- | --- | --- | --- |
| O01 | fixture_verified | claim binding, current-fact resolution and review recovery tests | validate against approved pilot facts and PostgreSQL |
| O02 | fixture_verified | parser, snapshot metadata and rule evidence tests | add live representative pages when authorized |
| O03 | fixture_verified | LangGraph resume and review outbox recovery tests | exercise with staging worker runtime |
| O04 | in_progress | constrained HTML publisher plus verification policy | connect authorized staging apply, callback, fresh fetch and rollback |
| O05 | fixture_verified | separated visibility metric definitions and fixtures | run authorized real Provider cohort |
| O06 | fixture_verified | CSV/PDF ingestion, source hash and locator tests | ingest approved business materials and measure retrieval |
| O07 | blocked_external | JWT protocol tests; local actor is not production identity | configure real OIDC and complete login/workspace role journey |
| O08 | in_progress | model ledger and worker recovery paths | finish fairness/cost reconciliation under real runtime |
| O09 | blocked_external | synthetic manifest only; zero independent human cases | run blinded 30-case holdout with independent reviewers |
| O10 | in_progress | migrations, audit hooks, API/UI builds and tests | prove clean PostgreSQL/Docker startup, worker consumption and recovery |

## Critical Cases

| case_id | status | evidence | next_action |
| --- | --- | --- | --- |
| C01-C05 | fixture_verified | claim/fact lifecycle regression tests | rerun on pilot facts and PostgreSQL |
| C06 | fixture_verified | shared document parser regression set | add authorized live fragments |
| C07 | fixture_verified | independent visibility metric cases | execute real Provider samples |
| C08-C11 | fixture_verified | review resume, lease fencing, idempotency and restart tests | validate with staging worker/outbox |
| C12 | fixture_verified | signed-token and workspace authorization fixtures | configure real issuer and memberships |
| C13-C14 | fixture_verified | constrained publisher and deployment verification unit tests | perform authorized staging page apply/fetch/rollback |
| C15 | blocked_external | synthetic fixtures only; no independent human evaluation | obtain holdout and reviewers |
| C16 | blocked_external | source readiness checks only | run full container and PostgreSQL runtime |

## Evidence boundary

No production claim is made from fixtures, synthetic samples, local Git commits,
or protocol-only identity tests. Real OIDC, Provider, staging/production write
scope, PostgreSQL/Docker runtime, approved business documents, independent
reviewers, and analytics/CRM data remain explicit external inputs in `INPUTS.md`.
