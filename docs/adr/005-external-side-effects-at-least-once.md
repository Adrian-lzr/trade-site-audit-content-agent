# ADR 005: Treat external writes as at-least-once

## Decision

Use a durable outbox, business idempotency keys, leases, and remote-state
reconciliation for publication and rollback. Do not claim exactly-once external
writes.

## Reason

The local database transaction cannot include a remote Git, CMS, or deployment
transaction. A process can stop after the remote accepts a request and before
the local acknowledgement is stored.

## Consequence

Retries first inspect the recorded or remote state. A changed current commit
causes a guarded rollback conflict instead of overwriting a human change.
