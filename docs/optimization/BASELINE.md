# Optimization Baseline

Recorded: 2026-10-02 (Asia/Shanghai)

## Source and Environment

| Item | Observed value |
| --- | --- |
| Repository | `Adrian-lzr/trade-site-audit-content-agent` |
| Starting commit | `2356fa39898d1495bdbd95ae65a03486f197d70a` |
| Starting branch | `main` |
| Optimization branch | `codex/optimization-20261002` |
| Starting worktree | clean before branch creation |
| Backend Python | 3.12.10 |
| Node.js / npm | 22.16.0 / 10.9.2 |
| Alembic head | `0014_memberships_audit_events_snapshot_metadata` |
| Backend dependency lock SHA-256 | `9cb65be0706a34e2377596258f7fb65063a2fd27397cf7e85e6fdb65bd63b399` |
| Web dependency lock SHA-256 | `71107fe5430e41af2d9f8db35000a572ce1110ad75604ddda4f5c3daaf6d01ee` |

## Baseline Checks

| Check | Result | Notes |
| --- | --- | --- |
| `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider backend/tests -q` | passed, 144 tests, 43.55 s | Uses `backend/tests/conftest.py`, which allocates a unique temporary SQLite file and upgrades it to Alembic head before tests. It does not target a PostgreSQL database. |
| `backend/.venv/Scripts/python.exe -m pytest -p no:cacheprovider evals/tests -q` | passed, 7 tests, 0.07 s | Existing evaluation pipeline only. |
| `npm ci --prefix apps/web` | passed | 25 packages added; npm audit reported 0 vulnerabilities. |
| `npm --prefix apps/web run build` | passed | TypeScript build and Vite 8.3.1 production build. |
| `backend/.venv/Scripts/python.exe -m alembic -c backend/alembic.ini heads` | passed | Single head `0014_memberships_audit_events_snapshot_metadata`. |
| `git diff --check` | passed | Baseline tree was clean. |
| Docker daemon check | unavailable | `docker info --format '{{.ServerVersion}}'` failed because the Docker Desktop Linux engine named pipe is absent. |
| PostgreSQL integration | not run | No isolated DSN configured. |
| Real site write / Provider / human evaluation | not run | External inputs are listed in `INPUTS.md`. |

## B01-B10 Traceability

These are code-level baseline observations from the fixed starting commit.
The behavior will be converted to regression cases before being marked fixed.

| Baseline | Observation | Planned task | Evidence / test to add |
| --- | --- | --- | --- |
| B01 | `validate_structured_draft` compares numeric tokens against all fact text, not claim attributes/units; it can miss unsupported semantic claims and reject unrelated valid numbers. | T04 | C01-C03 plus positive legal-MOQ and explicit-unit cases through the real workflow. |
| B02 | Fact has append-only `series_id` and `version`, but `_fact_current` checks status/time per row; it does not resolve the active version across a fact series or invalidate approvals on replacement. | T01, T03 | ORM tests for current, future, expired, revoked, conflicting and stale approved fact bindings. |
| B03 | `_sample_cites_site` falls back to `mentioned_domains_json`; an answer mentioning the site may count as a citation. | T05 | Mention-only/no-citation, native citation, inferred-link, and malicious-host metric cases. |
| B04 | HTML title/canonical extraction is distributed in the crawler/audit path; no shared document parser with explicit document-head semantics is present. | T02 | 15 frozen fragments including SVG/title, multiple title tags, body metadata, canonical variants and malformed HTML. |
| B05 | UTC helpers exist in selected paths, but temporal comparison policy is not centralized; SQLite test engine does not enable foreign-key enforcement on every connection. | T01 | Real Fact approval-to-publication ORM path, datetime boundaries, FK and workspace-scope constraint tests. |
| B06 | Current publication path creates a local Git/review artifact; it does not apply approved fields to a constrained HTML document and reread the page. | T12, T13 | HTML adapter diff-preservation and deployed-page inspect/rollback integration tests. |
| B07 | Fixture and generic HTTP Provider adapters exist; no authorized real Provider or consumer search-surface evidence is configured. | T14 | Contract fixtures plus separately sourced live Provider evidence. |
| B08 | Existing evaluation is a 40-case synthetic fixture; completion matrix records 0 human-complete cases. | T00, T15 | Freeze a separate labeled development/holdout manifest; real blind review requires independent people and source material. |
| B09 | `X-Local-User` is an actor assertion for local/demo use; production token validation/login is absent. | T09 | Signed-token tests for algorithm, issuer, audience, expiry, subject, session and cross-workspace membership. |
| B10 | Visibility budgets exist, but content and visibility calls lack a unified durable cost ledger; queue fairness and actual container runtime were not proved by the source gates. | T07, T08, T17 | Model-call/accounting fault cases, queue starvation/load tests, image build and full Compose worker-consumption run. |

No real customer data, live site mutation, real Provider request, PostgreSQL
integration DSN, or human annotation was used in this baseline. T00 remains in
progress until the B01-B10 regression inputs and separate synthetic holdout
manifest have been frozen without relabeling prior fixture cases.
