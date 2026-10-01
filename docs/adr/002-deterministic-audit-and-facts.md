# ADR 002: Deterministic checks and confirmed facts

## Decision

Run technical rules against immutable page snapshots and permit public content
claims only from current, confirmed, public workspace facts.

## Reason

The same snapshot and rule version must produce the same finding. External
guidance can explain a rule but cannot fill a missing company specification,
certificate, price, or delivery promise.

## Consequence

Missing or stale evidence pauses a content task. The UI can show a suggestion,
but the backend remains the authority for fact status and visibility.
