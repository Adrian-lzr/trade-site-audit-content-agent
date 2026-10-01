# ADR 004: Keep visibility providers separate from content generation

## Decision

`VisibilityProvider` is distinct from `ModelGateway` and declares
`model_api`, `consumer_search_surface`, or `manual_capture`. It persists raw
responses, parsed answers, citations, domains, capability metadata, and
cost/error fields.

## Reason

A normal model completion is not evidence of a consumer search result. Market,
language, citation support, and network behavior vary by provider and must be
visible in every run.

## Consequence

The fixture is explicitly synthetic. Missing credentials produce
`unavailable`, and failed samples are excluded from mention-rate denominators.
