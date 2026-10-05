# Optimization Inputs

This file records configuration names and non-sensitive status only. Do not add
credentials, personal data, customer records, or private source content here.

Updated: 2026-10-05 (Asia/Shanghai)

| Input | Status | Notes |
| --- | --- | --- |
| Source repository | available | `Adrian-lzr/trade-site-audit-content-agent`; local optimization branch created from `2356fa39898d1495bdbd95ae65a03486f197d70a` |
| Python | available | Python 3.12.10; locked backend environment exists |
| Node.js | available | Node 22.16.0; npm 10.9.2 |
| PostgreSQL 16 integration target | not_configured | No isolated integration DSN supplied; existing tests use a temporary SQLite database |
| Docker daemon | verified_local | Docker Desktop 29.8.1 / BuildKit responded on 2026-10-05; application image build remains blocked by Docker Hub token connectivity |
| OIDC issuer/audience/JWKS | not_configured | Real identity configuration has not been supplied |
| Production or staging site write scope | not_configured | Previous site work was read-only; no writable staging target or page mapping is supplied |
| Real visibility Provider | not_configured | No authorized endpoint/model configuration or budget supplied |
| Business source documents | not_supplied | No approved product documents for the pilot have been supplied in this task |
| Independent evaluation reviewers | not_supplied | No reviewers or real holdout set supplied; synthetic evaluation must remain labeled synthetic |
| Search Console / analytics / CRM export | not_supplied | No business outcome source or reporting window supplied |
| Production deployment target | adapter_ready_external_unverified | Signed callback and configurable fresh target observer are implemented; no authorized callback secret, staging target, or rollback environment supplied |
| Read-only site reachability | observed | Playwright opened both sites on 2026-10-02. Bounded live HTTPS audits on 2026-10-05 sampled 10 pages and then 15 pages per site; the 15-page run had 180 rule results per site, both jobs succeeded, and no CMS, form, publication, PR, or deployment endpoint was called. A separate parser source sample made 10 public GET requests and retained only URL/status/hash/parser metadata, not raw HTML. Evidence: `output/online-audit/zoogo-sites-20261005.json`, `output/online-audit/zoogo-sites-20261005-page15.json`, and `output/online-audit/zoogo-source-samples-20261005.json`. This remains read-only reachability and parser regression input, not production authorization, confirmed enterprise facts, or full-site approval. |

Real secrets must be supplied through environment variables or a secret manager
when a corresponding authorized test is ready. This file must contain only
non-sensitive names, status, and safe identifiers.
