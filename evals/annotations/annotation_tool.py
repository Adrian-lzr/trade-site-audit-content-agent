"""Validate and summarize Phase 6 human annotation records.

The tool deliberately keeps the annotation set independent from the synthetic
fixture benchmark.  A case only contributes to quality and decision metrics
when all required annotation fields are present.  Missing labels are reported
as incomplete and are never converted to a zero score.

Examples (from the repository root)::

    python evals/annotations/annotation_tool.py validate annotations.json
    python evals/annotations/annotation_tool.py summary annotations.json
    python evals/annotations/annotation_tool.py summary annotations.json \
        --output evals/artifacts/phase6-human-summary.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "1.1"
MIN_CASES = 30
MAX_CASES = 50
STRATEGIES = ("original", "ordinary_rewrite", "fact_constrained_rewrite")
SPLITS = ("dev", "holdout")
LABEL_FIELDS = (
    "factual_consistency",
    "procurement_question_coverage",
    "readability",
    "actionability",
)
DECISIONS = ("adopted", "adopted_after_edit", "rejected")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class ValidationIssue:
    """A path-addressed validation message."""

    path: str
    message: str


@dataclass(frozen=True)
class ValidationReport:
    """Separate malformed data from valid-but-unfinished annotation work."""

    schema_valid: bool
    complete: bool
    case_count: int
    complete_case_count: int
    incomplete_case_count: int
    errors: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    incomplete: tuple[ValidationIssue, ...] = field(default_factory=tuple)
    warnings: tuple[ValidationIssue, ...] = field(default_factory=tuple)

    @property
    def valid(self) -> bool:
        """Return whether the JSON has no structural or value errors."""

        return self.schema_valid

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_valid": self.schema_valid,
            "complete": self.complete,
            "case_count": self.case_count,
            "complete_case_count": self.complete_case_count,
            "incomplete_case_count": self.incomplete_case_count,
            "errors": [asdict(issue) for issue in self.errors],
            "incomplete": [asdict(issue) for issue in self.incomplete],
            "warnings": [asdict(issue) for issue in self.warnings],
        }


def load_annotations(path: str | Path) -> dict[str, Any]:
    """Load one annotation JSON document and reject non-object roots."""

    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("annotation document root must be a JSON object")
    return value


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _issue(path: str, message: str) -> ValidationIssue:
    return ValidationIssue(path, message)


def _validate_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _validate_snapshot(
    value: Any,
    path: str,
    incomplete: list[ValidationIssue],
    errors: list[ValidationIssue],
) -> None:
    if not isinstance(value, dict):
        errors.append(_issue(path, "must be an object"))
        return
    for key in ("html_sha256", "facts_sha256", "source_locator"):
        item_path = f"{path}.{key}"
        item = value.get(key)
        if not _text(item):
            incomplete.append(_issue(item_path, "required for a complete case"))
        elif key.endswith("sha256") and not _SHA256.fullmatch(item.strip()):
            errors.append(_issue(item_path, "must be a 64-character SHA-256 hex digest"))


def _validate_case(
    case: Any,
    index: int,
    seen_ids: set[str],
) -> tuple[bool, list[ValidationIssue], list[ValidationIssue], list[ValidationIssue]]:
    """Validate one row and return (complete, errors, incomplete, warnings)."""

    path = f"cases[{index}]"
    errors: list[ValidationIssue] = []
    incomplete: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if not isinstance(case, dict):
        errors.append(_issue(path, "must be an object"))
        return False, errors, incomplete, warnings

    case_id = case.get("case_id")
    if case_id is None or (isinstance(case_id, str) and not case_id.strip()):
        incomplete.append(_issue(f"{path}.case_id", "required for a complete case"))
    elif not isinstance(case_id, str):
        errors.append(_issue(f"{path}.case_id", "must be a non-empty string"))
    elif case_id in seen_ids:
        errors.append(_issue(f"{path}.case_id", f"duplicate case_id: {case_id}"))
    else:
        seen_ids.add(case_id)
    if isinstance(case_id, str) and case_id.lower().startswith("replace-with-"):
        incomplete.append(_issue(f"{path}.case_id", "placeholder must be replaced"))

    split = case.get("split")
    if split not in SPLITS:
        if split in (None, ""):
            incomplete.append(_issue(f"{path}.split", "must be dev or holdout"))
        else:
            errors.append(_issue(f"{path}.split", "must be dev or holdout"))

    strategy = case.get("strategy")
    if strategy not in STRATEGIES:
        if strategy in (None, ""):
            incomplete.append(_issue(f"{path}.strategy", "must identify one comparison strategy"))
        else:
            errors.append(_issue(f"{path}.strategy", f"must be one of: {', '.join(STRATEGIES)}"))

    for key, label in (("page_id", "stable page ID"), ("buyer_question_id", "fixed buyer-question ID")):
        value = case.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            incomplete.append(_issue(f"{path}.{key}", f"{label} is required for a complete case"))
        elif not isinstance(value, str):
            errors.append(_issue(f"{path}.{key}", "must be a non-empty string"))

    _validate_snapshot(case.get("source_snapshot"), f"{path}.source_snapshot", incomplete, errors)

    annotator = case.get("annotator")
    if not _text(annotator):
        incomplete.append(_issue(f"{path}.annotator", "required; use a reviewer code if needed"))
    annotated_at = case.get("annotated_at")
    if not _validate_timestamp(annotated_at):
        incomplete.append(_issue(f"{path}.annotated_at", "required ISO-8601 timestamp with timezone"))

    labels = case.get("labels")
    if not isinstance(labels, dict):
        if labels is None:
            incomplete.append(_issue(f"{path}.labels", "all four labels are required"))
        else:
            errors.append(_issue(f"{path}.labels", "must be an object"))
    else:
        for field_name in LABEL_FIELDS:
            label_path = f"{path}.labels.{field_name}"
            value = labels.get(field_name)
            if value is None:
                incomplete.append(_issue(label_path, "required score from 1 to 5"))
            elif isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                errors.append(_issue(label_path, "must be an integer from 1 to 5"))

    decision = case.get("decision")
    if decision not in DECISIONS:
        if decision in (None, ""):
            incomplete.append(_issue(f"{path}.decision", f"required; choose one of: {', '.join(DECISIONS)}"))
        else:
            errors.append(_issue(f"{path}.decision", f"must be one of: {', '.join(DECISIONS)}"))
    reason = case.get("decision_reason")
    if decision in ("adopted_after_edit", "rejected") and not _text(reason):
        incomplete.append(_issue(f"{path}.decision_reason", "required for this decision"))
    elif reason is not None and not isinstance(reason, str):
        errors.append(_issue(f"{path}.decision_reason", "must be a string when provided"))

    # A non-empty note is useful, but never blocks completion.
    if "notes" in case and case["notes"] is not None and not isinstance(case["notes"], str):
        errors.append(_issue(f"{path}.notes", "must be a string when provided"))

    complete = not errors and not incomplete
    if not complete and not errors:
        warnings.append(_issue(path, "record is incomplete and will be excluded from metrics"))
    return complete, errors, incomplete, warnings


def validate_dataset(data: Mapping[str, Any]) -> ValidationReport:
    """Validate an annotation document without treating missing labels as zeros."""

    errors: list[ValidationIssue] = []
    incomplete: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    if not isinstance(data, Mapping):
        return ValidationReport(False, False, 0, 0, 0, (_issue("$", "root must be an object"),))

    for key in ("dataset_version", "label_schema_version"):
        if not _text(data.get(key)):
            incomplete.append(_issue(key, "required"))
    status = data.get("annotation_status")
    if status not in ("template_pending_review", "in_progress", "complete"):
        if status in (None, ""):
            incomplete.append(_issue("annotation_status", "required"))
        else:
            errors.append(_issue("annotation_status", "must be template_pending_review, in_progress, or complete"))

    comparison = data.get("comparison")
    if not isinstance(comparison, dict):
        if comparison is None:
            incomplete.append(_issue("comparison", "dataset, model, and prompt provenance is required"))
        else:
            errors.append(_issue("comparison", "must be an object"))
    else:
        for key, label in (("page_ids", "stable page ID"), ("question_ids", "fixed buyer-question ID")):
            values = comparison.get(key)
            if not isinstance(values, list) or not values or any(not _text(item) for item in values):
                incomplete.append(_issue(f"comparison.{key}", f"at least one {label} is required"))
        for key in ("question_set_version", "model_version", "prompt_version"):
            if not _text(comparison.get(key)):
                incomplete.append(_issue(f"comparison.{key}", "required provenance field"))

    cases = data.get("cases")
    if not isinstance(cases, list):
        errors.append(_issue("cases", "must be an array"))
        return ValidationReport(False, False, 0, 0, 0, tuple(errors), tuple(incomplete), tuple(warnings))

    case_count = len(cases)
    if case_count < MIN_CASES:
        incomplete.append(_issue("cases", f"at least {MIN_CASES} cases are required; found {case_count}"))
    elif case_count > MAX_CASES:
        incomplete.append(_issue("cases", f"at most {MAX_CASES} cases are allowed; found {case_count}"))

    seen_ids: set[str] = set()
    complete_count = 0
    complete_cases: list[Mapping[str, Any]] = []
    for index, case in enumerate(cases):
        complete, case_errors, case_incomplete, case_warnings = _validate_case(case, index, seen_ids)
        errors.extend(case_errors)
        incomplete.extend(case_incomplete)
        warnings.extend(case_warnings)
        if complete:
            complete_count += 1
            if isinstance(case, Mapping):
                complete_cases.append(case)

    # The holdout list is an auditable freeze marker, separate from case splits.
    holdout_ids = data.get("holdout_case_ids")
    if not isinstance(holdout_ids, list) or not holdout_ids:
        incomplete.append(_issue("holdout_case_ids", "a non-empty frozen holdout list is required"))
    elif any(not _text(item) for item in holdout_ids):
        errors.append(_issue("holdout_case_ids", "all IDs must be non-empty strings"))
    elif len(set(holdout_ids)) != len(holdout_ids):
        errors.append(_issue("holdout_case_ids", "IDs must be unique"))
    else:
        actual_holdout = {
            case.get("case_id")
            for case in cases
            if isinstance(case, Mapping) and case.get("split") == "holdout"
        }
        declared_holdout = set(holdout_ids)
        if actual_holdout != declared_holdout:
            errors.append(_issue("holdout_case_ids", "must exactly match case IDs whose split is holdout"))
    if not _validate_timestamp(data.get("holdout_frozen_at")):
        incomplete.append(_issue("holdout_frozen_at", "required ISO-8601 timestamp with timezone"))

    required_strategies = data.get("strategies", list(STRATEGIES))
    if not isinstance(required_strategies, list) or not required_strategies:
        errors.append(_issue("strategies", "must be a non-empty array"))
        required_strategies = list(STRATEGIES)
    elif any(strategy not in STRATEGIES for strategy in required_strategies):
        errors.append(_issue("strategies", f"entries must be one of: {', '.join(STRATEGIES)}"))
    represented = {case.get("strategy") for case in complete_cases}
    for strategy in required_strategies:
        if strategy not in represented:
            incomplete.append(_issue("strategies", f"no complete case recorded for {strategy}"))

    if status == "complete" and (errors or incomplete or complete_count < MIN_CASES or complete_count > MAX_CASES):
        errors.append(_issue("annotation_status", "cannot be complete while validation gaps remain"))
    complete = not errors and not incomplete and MIN_CASES <= complete_count <= MAX_CASES
    return ValidationReport(
        schema_valid=not errors,
        complete=complete,
        case_count=case_count,
        complete_case_count=complete_count,
        incomplete_case_count=case_count - complete_count,
        errors=tuple(errors),
        incomplete=tuple(incomplete),
        warnings=tuple(warnings),
    )


def _mean(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def summarize_dataset(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe summary; incomplete rows never enter score means."""

    report = validate_dataset(data)
    cases = data.get("cases") if isinstance(data.get("cases"), list) else []
    # Validate rows by index so duplicate IDs and repeated dictionaries remain
    # distinguishable in the summary.
    complete_indexes = {
        int(issue.path.split("[")[1].split("]")[0])
        for issue in report.errors + report.incomplete
        if issue.path.startswith("cases[") and issue.path.split("]")[0][6:].isdigit()
    }
    complete_cases = [case for index, case in enumerate(cases) if index not in complete_indexes and isinstance(case, Mapping)]

    by_strategy: dict[str, dict[str, Any]] = {}
    for strategy in STRATEGIES:
        selected = [case for case in complete_cases if case.get("strategy") == strategy]
        score_means = {
            field_name: _mean([case["labels"][field_name] for case in selected])
            for field_name in LABEL_FIELDS
        }
        decisions = {decision: 0 for decision in DECISIONS}
        for case in selected:
            decision = case.get("decision")
            if decision in decisions:
                decisions[decision] += 1
        by_strategy[strategy] = {
            "complete_case_count": len(selected),
            "incomplete_case_count": sum(
                1 for case in cases if isinstance(case, Mapping) and case.get("strategy") == strategy
            ) - len(selected),
            "label_means": score_means,
            "label_4_or_5_rates": {
                field_name: (
                    round(sum(case["labels"][field_name] >= 4 for case in selected) / len(selected), 4)
                    if selected
                    else None
                )
                for field_name in LABEL_FIELDS
            },
            "decision_counts": decisions,
        }

    decision_counts = {decision: 0 for decision in DECISIONS}
    for case in complete_cases:
        decision = case.get("decision")
        if decision in decision_counts:
            decision_counts[decision] += 1
    return {
        "dataset_version": data.get("dataset_version"),
        "label_schema_version": data.get("label_schema_version"),
        "annotation_status_declared": data.get("annotation_status"),
        "complete": report.complete,
        "sample_count": len(cases),
        "complete_case_count": len(complete_cases),
        "incomplete_case_count": len(cases) - len(complete_cases),
        "required_case_count": {"min": MIN_CASES, "max": MAX_CASES},
        "remaining_to_minimum": max(0, MIN_CASES - len(complete_cases)),
        "holdout_case_count": sum(
            1 for case in cases if isinstance(case, Mapping) and case.get("split") == "holdout"
        ),
        "decision_counts": decision_counts,
        "strategies": by_strategy,
        "validation": report.as_dict(),
        "metric_basis": "complete_cases_only; incomplete labels are excluded, never scored as zero",
    }


def _render_report(report: ValidationReport) -> str:
    lines = [
        f"schema_valid: {'yes' if report.schema_valid else 'no'}",
        f"complete: {'yes' if report.complete else 'no'}",
        f"cases: {report.case_count} total, {report.complete_case_count} complete, {report.incomplete_case_count} incomplete",
    ]
    if report.errors:
        lines.append("errors:")
        lines.extend(f"  - {issue.path}: {issue.message}" for issue in report.errors)
    if report.incomplete:
        lines.append("incomplete:")
        lines.extend(f"  - {issue.path}: {issue.message}" for issue in report.incomplete)
    if report.warnings:
        lines.append("warnings:")
        lines.extend(f"  - {issue.path}: {issue.message}" for issue in report.warnings)
    return "\n".join(lines)


def _write_json(value: Mapping[str, Any], output: Path | None) -> None:
    encoded = json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "summary", "summarize"):
        command = subparsers.add_parser(name, help=f"{name} an annotation JSON file")
        command.add_argument("input", type=Path)
        command.add_argument("--json", action="store_true", help="emit a machine-readable report")
        command.add_argument("--output", type=Path, default=None, help="write a summary/report JSON file")
        command.add_argument(
            "--allow-incomplete",
            action="store_true",
            help="return success for a structurally valid but unfinished set",
        )
    args = parser.parse_args(argv)
    try:
        data = load_annotations(args.input)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    report = validate_dataset(data)
    if args.command == "validate":
        if args.json or args.output:
            _write_json(report.as_dict(), args.output)
        else:
            print(_render_report(report))
    else:
        summary = summarize_dataset(data)
        _write_json(summary, args.output)

    if not report.schema_valid:
        return 2
    if not report.complete and not args.allow_incomplete:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
