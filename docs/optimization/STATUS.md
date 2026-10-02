# Optimization Status

Updated: 2026-10-02 (Asia/Shanghai)

Task status values are `not_started`, `in_progress`, `code_done`,
`fixture_verified`, `real_verified`, `blocked_external`, and `failed`.
An item is not complete merely because code or a report file exists.

| task_id | status | commit | checks | evidence | blocker | next_action |
| --- | --- | --- | --- | --- | --- | --- |
| T00 | in_progress | pending | backend 144 passed; eval 7 passed; npm ci; web build; Alembic head inspected | `BASELINE.md` | real holdout and PostgreSQL integration target not supplied | freeze B01-B10 regressions and an explicitly synthetic separate holdout manifest |
| T01 | in_progress | pending | baseline only | `BASELINE.md` | PostgreSQL target not supplied | centralize UTC policy and SQLite FK enforcement; add real Fact ORM tests |
| T02 | in_progress | pending | baseline only | `BASELINE.md` | none for parser fixtures | fix shared parsing and audit summary; delegated bounded implementation |
| T03 | in_progress | pending | baseline only | `BASELINE.md` | PostgreSQL concurrency target not supplied | implement one current-fact resolver and stale approval checks |
| T04 | not_started | pending | baseline only | `BASELINE.md` | depends on T03 | add typed claims and server-side fact binding validation |
| T05 | in_progress | pending | baseline only | `BASELINE.md` | no real Provider needed for pure metric fixture | separate mention, citation, coverage, and success metrics |
| T06 | not_started | - | not_run | - | depends on T02-T05 | extract application services and freeze API contract |
| T07 | not_started | - | not_run | - | real Provider required for real usage evidence | persist calls, costs, reservations, and unknown outcomes |
| T08 | not_started | - | not_run | - | depends on T03, T07 | make workflow review recovery and fair worker claims explicit |
| T09 | blocked_external | - | not_run | `INPUTS.md` | OIDC issuer/audience/JWKS and deployment topology not supplied | implement protocol adapter with fixtures; retain real identity gate |
| T10 | not_started | - | not_run | - | business documents not supplied | add source-backed PDF/CSV ingestion and 30-question retrieval set |
| T11 | not_started | - | not_run | - | depends on T04, T08, T09, T10 | complete evidence-centered UI journey |
| T12 | not_started | - | not_run | - | depends on T03, T04, T06, T08, T09 | implement constrained static HTML adapter |
| T13 | not_started | - | not_run | - | writable staging target not supplied | verify deployed page and guarded rollback |
| T14 | blocked_external | - | not_run | `INPUTS.md` | authorized Provider and budget not supplied | complete adapter fixtures; run real 30-sample cohort when configured |
| T15 | blocked_external | - | not_run | `INPUTS.md` | real 50-case set and independent reviewers not supplied | freeze a separate synthetic dev/holdout set; do not claim human results |
| T16 | not_started | - | not_run | - | depends on call/worker IDs | correlate audit, workflow, model, publication, and sample events |
| T17 | not_started | - | not_run | - | Docker daemon and PostgreSQL integration runtime unverified | add isolated PostgreSQL CI and actual container runtime harness |
| T18 | blocked_external | - | not_run | `INPUTS.md` | authorized analytics/CRM source and observation window not supplied | implement cohort-safe fixture reports without business claims |
| T19 | not_started | - | not_run | - | depends on all tasks | produce O/C matrices, runtime/evaluation docs, and final evidence pack |

## Milestones

| milestone | status | evidence |
| --- | --- | --- |
| M0 | in_progress | T00-T05 are not yet all verified |
| M1 | not_started | T06-T14, T16-T17 remain |
| M2 | blocked_external | real identity, site, Provider, independent review, and deployment inputs remain |
| M3 | blocked_external | requires M2 plus a separately measured observation window |

## Goal Matrix

| goal | status | evidence | next_action |
| --- | --- | --- | --- |
| O01 | in_progress | B01/B02 in `BASELINE.md` | T01, T03, T04 |
| O02 | in_progress | B04 in `BASELINE.md` | T02, T10 |
| O03 | not_started | workflow behavior needs new failure-injection evidence | T06-T08, T11 |
| O04 | not_started | current publisher is a local rehearsal | T12, T13, T17 |
| O05 | in_progress | B03 in `BASELINE.md` | T05, T14, T18 |
| O06 | not_started | existing facts have locators; document ingestion/recall target not met | T03, T04, T10 |
| O07 | blocked_external | local actor assertion is not verified identity | T06, T09, T11 |
| O08 | not_started | existing visibility budgets do not form a unified model-call ledger | T07, T08, T14, T16 |
| O09 | blocked_external | 0 independent human-labeled cases | T00, T15, T18, T19 |
| O10 | in_progress | source gates pass; actual clean runtime/build evidence is incomplete | T00, T01, T06, T16, T17, T19 |

## Critical Cases

| case_id | status | evidence | next_action |
| --- | --- | --- | --- |
| C01-C04 | not_started | no new regression run | T03-T04 |
| C05 | not_started | existing smoke does not prove target Fact expiry path | T01, T17 |
| C06 | in_progress | parser work delegated | T02 |
| C07 | in_progress | metric module work delegated | T05 |
| C08-C11 | not_started | existing partial lease/idempotency tests do not cover all fault windows | T07-T08, T17 |
| C12 | not_started | local Membership tests exist; no verified JWT path | T09 |
| C13-C14 | not_started | local Git readiness is not page application/deployment verification | T12-T13 |
| C15 | blocked_external | synthetic fixtures only; human evaluation not complete | T15, T18, T19 |
| C16 | not_started | prior smoke did not validate full container runtime and worker consumption | T17 |
