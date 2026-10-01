# ADR 003: Approval binds a concrete revision

## Decision

An approval records the revision ID, content hash, page snapshot baseline, and
fact versions. A later revision or changed baseline invalidates the approval.

## Reason

Reviewing one diff and publishing another is an unsafe and hard-to-audit
operation. Optimistic version checks also protect concurrent human edits.

## Consequence

The publish endpoint rechecks the same conditions immediately before enqueueing
the side effect and returns a conflict when they changed.
