# Phase 6 Human Annotation Workflow

The production plan calls for 30-50 HTML and enterprise-fact cases with an
independent human label and a fixed holdout. The checked-in benchmark in
`../visibility_eval.py` is a deterministic synthetic recipe and is kept
separate from that requirement.

`phase6-human-labels.template.json` is a starting format, not a completed
dataset. Copy it to a working file, replace the placeholder row with 30-50
stable case rows, and keep the holdout frozen before comparing strategies.
Each row represents one strategy result (`original`, `ordinary_rewrite`, or
`fact_constrained_rewrite`) for a source HTML/fact snapshot.

## Record Format

Required case evidence and reviewer metadata:

- `case_id`, `split` (`dev` or `holdout`), `strategy`, `page_id`, and
  `buyer_question_id` from the frozen page/question mapping
- `source_snapshot.html_sha256`, `source_snapshot.facts_sha256`, and
  `source_snapshot.source_locator`
- `annotator` (a person name or pseudonymous reviewer code) and
  `annotated_at` (ISO-8601 timestamp with timezone)
- Four integer labels from 1 (poor) to 5 (strong):
  `factual_consistency`, `procurement_question_coverage`, `readability`, and
  `actionability`
- `decision`: `adopted`, `adopted_after_edit`, or `rejected`
- `decision_reason` is required for `adopted_after_edit` and `rejected`;
  explain what changed or why the result was not usable.

The top-level `holdout_case_ids` and `holdout_frozen_at` fields are the audit
marker for the fixed holdout. `comparison.page_ids`,
`comparison.question_ids`, `comparison.model_version`,
`comparison.prompt_version`, and `comparison.question_set_version` preserve
the page/question mapping and provenance used for the comparison.

## Validate And Summarize

Run from the repository root with the project Python interpreter:

```powershell
backend\.venv\Scripts\python.exe evals\annotations\annotation_tool.py validate path\to\annotations.json
backend\.venv\Scripts\python.exe evals\annotations\annotation_tool.py summary path\to\annotations.json
```

`validate` exits with code 2 for malformed JSON/schema values and code 1 when
the document is structurally valid but unfinished. Add `--allow-incomplete`
while entering labels if a zero exit code is convenient. The summary command
prints JSON and accepts `--output path\to\summary.json`; it also exits 1 until
the required 30-50 complete cases and frozen holdout are present.

Example while setting up a file:

```powershell
backend\.venv\Scripts\python.exe evals\annotations\annotation_tool.py validate `
  evals\annotations\phase6-human-labels.template.json --allow-incomplete
```

The summary reports total, complete, and incomplete rows, per-strategy label
means and 4-or-5 rates, and decision counts. **Incomplete rows are excluded
from every quality metric; a missing label is never treated as zero or as an
accepted review.** The top-level `complete` flag is true only when validation,
the case-count range, all declared strategies, and the frozen holdout checks
pass.

## Review Protocol

Use the same question set, model configuration, and prompt version for the
three strategies. Blind the strategy name during review when practical, then
record the reviewer code and timestamp after the decision. Keep source hashes
and the raw candidate text alongside the annotation file or in the artifact
store referenced by `source_locator`. Do not overwrite old annotations when a
review is corrected; create a new dataset version and document the change.

Until `annotation_tool.py summary` reports `"complete": true`, Phase 6 reports
must describe this directory as an annotation template or in-progress set and
must not claim a human-evaluated benchmark or human adoption rate.

The aggregate audit is `..\phase6_completion.py`. It reads this annotation
file and the synthetic fixture artifact, then writes a completion matrix with
separate 10.1 fixture, 10.1 human, 10.2 comparison, and deliverable checks.
Its overall result is intentionally incomplete while this template has zero
complete rows.
