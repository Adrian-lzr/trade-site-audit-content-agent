# External input audit (2026-10-05)

This note records a bounded, read-only check of public sources that could help
exercise T14 (visibility Provider), T15 (fair evaluation), and T18 (historical
business reporting). It does not authorize either supplied site, a Provider,
an analytics account, a CRM, or a production deployment.

The probe made only HTTPS `GET` requests. It did not call a model-generation
endpoint, submit a form, use credentials, contact `zoogo.club` or
`zoogosports.com`, or retain response bodies. The artifact stores response
status, final URL, byte count, SHA-256, and limited JSON shape metadata:

`output/optimization/T14-T15-T18/public-readonly-source-audit-20261005.json`

The public probes were:

| Source | Observation | Permitted local use |
| --- | --- | --- |
| DuckDuckGo Instant Answer API | HTTP 200, but the tested procurement query returned no abstract or related results | Parser/empty-result handling only; not a Provider answer or citation sample |
| Hugging Face model catalog | HTTP 200 model metadata | Model catalog metadata only; it does not run inference and does not prove a usable Provider |
| Google Search Console discovery document | HTTP 200; discovery metadata exposes OAuth2 auth | Protocol/documentation fixture only; no Search Console property data was read |
| Google Search Console authorization documentation | HTTP 200 | Authorization guidance only |
| Wikipedia API metadata probe | HTTP 403 | No usable public source was inferred from the failed request |

The repository boundary is explicit: `StructuredHTTPVisibilityProvider` is
available only when both an endpoint and credential are configured; without
them it returns `unavailable`. `FixtureVisibilityProvider` is synthetic and
must remain marked as such. Public pages or catalogs cannot supply native
Provider citations, usage, cost, an authorized consumer surface, or a real
answering cohort.

Therefore the current task states remain:

- **T14 `blocked_external`**: public data can exercise protocol/parser paths,
  but a real Provider endpoint, model/surface configuration, budget policy, and
  permission for at least 30 samples are still required.
- **T15 `blocked_external`**: public pages can be parser inputs only when
  labeled `public_web_unverified`; they cannot replace approved business
  facts, a frozen 20/30 split, 90 real workflow outputs, or independent human
  labels.
- **T18 `blocked_external`**: public API documentation can exercise auth and
  import failure paths, but Search Console/analytics/CRM exports, event
  definitions, fixed cohort metadata, and at least 28 days before/after data
  are still required.

No conclusion in this audit is a production, enterprise-fact, human-review,
Provider, CRM, or business-outcome claim.
