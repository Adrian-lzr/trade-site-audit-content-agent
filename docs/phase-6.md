# Phase 6: Evaluation and Delivery

## Scope

Phase 6 separates reproducible engineering checks, the offline content
benchmark, and any future online visibility observation. The repository does
not claim a search ranking, AI citation, traffic, or lead improvement from the
offline fixture.

## Frozen evaluation

The benchmark in [`../evals/visibility_eval.py`](../evals/visibility_eval.py)
contains 40 deterministic synthetic HTML and enterprise-fact cases. Cases
01-30 are the development split and cases 31-40 are a fixed holdout split.
Each run records `dataset_version`, `model_version`, `prompt_version`, sample
counts, factual consistency, procurement question coverage, forbidden-claim
absence, and mean score for:

- the original text;
- an ordinary rewrite;
- a fact-constrained procurement rewrite.

The generated evidence is stored in
`evals/artifacts/phase6-evaluation.json`. It is an offline fixture result, not
an observation from `zoogo.club`, `zoogosports.com`, a consumer search surface,
or a model API.

The artifact records local fixture wall-clock P50/P95 timings and a zero-cost
fixture basis. These fields are engineering measurements only; they are not a
production latency or billing estimate. The plan's independently human-labeled
30-50-case dataset is still pending. Its required schema and holdout policy
are in [`../evals/annotations/README.md`](../evals/annotations/README.md).

## Engineering gates

The final verification command is:

```powershell
backend\.venv\Scripts\python.exe -m pytest -p no:cacheprovider backend/tests -q
npm --prefix apps/web run build
backend\.venv\Scripts\python.exe -m alembic -c backend\alembic.ini upgrade head
git diff --check
```

The tests cover snapshot and rule determinism, fact visibility and version
guards, approval and publication idempotency, lease recovery, rollback
conflict protection, workspace isolation, and visibility fixture/unavailable
sampling. A passing suite is evidence of these contracts only; it is not a
production availability or online search-effectiveness certification.

## Delivery artifacts

- [Architecture decisions](adr/): five short decisions covering workflow
  boundaries, facts, approvals, providers, and external side effects.
- [Five-minute demo script](demo-script.md): a repeatable local walkthrough.
- [Offline evaluation runner](../evals/visibility_eval.py) and generated
  result: [phase6-evaluation.json](../evals/artifacts/phase6-evaluation.json).
- [Current Phase 6 measured report](phase-6-report.md), including the bounded
  read-only observations for the two named public sites.
- [Phase 5 visibility status](phase-5.md): source capabilities, fixture
  boundary, and online-provider limitations.
- [Architecture diagram](architecture.md) and [license inventory](license-inventory.md):
  runtime boundaries and the dependency metadata captured from lock files.

## Known limits

The structured provider is an integration boundary. Without an explicitly
configured endpoint and credential it returns `unavailable`; it does not
pretend that a normal model answer is a live search result. The two named
public sites remain read-only inputs. Remote CMS/PR writes, consumer search
surface automation, Search Console attribution, and business outcome claims
require separate authorization and evidence.
