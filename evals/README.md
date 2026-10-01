# Phase 6 Offline Evaluation

`visibility_eval.py` runs a deterministic, offline benchmark over 40 synthetic
HTML and enterprise-fact cases. Cases 1-30 are the development split and
cases 31-40 are the fixed holdout split. The recipe, dataset version, model
version and prompt version are all recorded in the JSON output.

Run it from the repository root:

```powershell
backend\.venv\Scripts\python.exe evals\visibility_eval.py --output evals\artifacts\phase6-evaluation.json
```

The three strategies are original content, an ordinary rewrite, and a
fact-constrained procurement rewrite. The result only measures this local
fixture benchmark. It does not measure search ranking, consumer AI citations,
traffic, leads, or business growth.

Each strategy also records local wall-clock `p50`/`p95` latency and a cost
record. The fixture cost is explicitly zero because no live model or provider
was called; these timings are not production latency estimates. The required
independent human annotation format lives in `annotations/` and is not implied
by the synthetic recipe.

To validate and summarize the independent human review set, run
`backend\\.venv\\Scripts\\python.exe evals\\annotations\\annotation_tool.py`
with the `validate` or `summary` command. See `annotations/README.md` for the
1-5 label rubric, decision fields, fixed holdout requirements, and the rule
that incomplete rows are excluded from metrics.

To audit the Phase 6 gates together, run:

```powershell
backend\.venv\Scripts\python.exe evals\phase6_completion.py `
  --output evals\artifacts\phase6-completion-matrix.json
```

The command exits `1` while the human annotation set is incomplete. The JSON
matrix keeps the synthetic fixture gate separate from the human-review and
10.2 strategy-comparison gates; `human_evaluation_claim_allowed` and
`adoption_rate_claim_allowed` remain false until the validator sees a complete
independent set.
