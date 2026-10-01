# ADR 001: Keep the workflow stages separate

## Decision

Keep crawling/auditing, fact-constrained drafting, approval, publication, and
visibility sampling as separate stages with narrow interfaces.

## Reason

An audit can be deterministic and replayed from a snapshot. Drafting needs
confirmed facts. Publication has external side effects. Visibility sampling
has a different evidence and cost model. Combining them would make retries,
permissions, and evidence difficult to inspect.

## Consequence

The API and workers pass IDs and small summaries between stages. Long HTML and
provider responses remain persisted evidence rather than graph state.
