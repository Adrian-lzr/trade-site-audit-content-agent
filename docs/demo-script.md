# Five-Minute Local Demo

This script uses the loopback fixture and synthetic facts. It never publishes
to a customer site.

## Reproducible seed evidence

Before starting the UI, create the local demo evidence in a SQLite database:

```powershell
backend\.venv\Scripts\python.exe scripts\seed_demo_data.py `
  --synthetic `
  --database-url sqlite:///./backend.db `
  --output evals/artifacts/phase1-demo-seed.json
```

The command is intentionally opt-in and refuses PostgreSQL, other non-SQLite
URLs, and an existing non-synthetic target site. It only reads the checked-in
`demo-site` fixture; it does not call `zoogo.club`, `zoogosports.com`, a CMS,
remote Git, or a provider. Re-running it reuses the same workspace, loopback
site, snapshots, facts, and frozen twenty-question version without creating
duplicates. The JSON report records the dataset/seed version, IDs, question
count, frozen state, fixture manifest hash, snapshot rows, source locators, and
validity metadata.

This report is local synthetic demonstration evidence. It is not real customer
information, a production database snapshot, an online publication, or proof
that any customer site was changed.

## 0:00-0:45: Start the services

1. Start the fixture server on `127.0.0.1:8765`.
2. Start the API and Worker with `ALLOW_LOOPBACK=true` and the same database.
3. Start the Vite app and open `http://127.0.0.1:5173`.

## 0:45-1:30: Capture an audit

Register the fixture site and run an audit. Open the page evidence and show the
missing-title finding. Explain that the finding points to a frozen snapshot and
rule version.

## 1:30-2:30: Show facts and a draft

Import the synthetic fact CSV, confirm one public fact, freeze the procurement
question set, and create a content task. Open the diff and the source locator.
Show that an internal, expired, or proposed fact cannot support a public claim.

## 2:30-3:30: Review and local publication

Approve the exact revision, enqueue the isolated Git publication, and show the
attempt, branch, commit, deployment fields, and verification state. Repeat the
request to show the idempotency record rather than a duplicate side effect.

## 3:30-4:20: Visibility sampling

Open `可见性监测`, select the frozen question set, and run the fixture provider.
Point out the `synthetic` capability label, raw answer, citation URL, brand
flag, and separate cost/status counters. Run the structured provider without
credentials to show `unavailable`, not a fabricated online result.

## 4:20-5:00: Evaluation boundary

Run `backend\\.venv\\Scripts\\python.exe evals\\visibility_eval.py` and open
`evals/artifacts/phase6-evaluation.json`. State the sample count, fixed
holdout, versions, and measured strategy scores. Close by stating that the
offline fixture does not prove rankings, AI citations, traffic, or leads.
