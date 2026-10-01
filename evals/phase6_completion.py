"""Produce a machine-checkable Phase 6 completion matrix.

This audit keeps the synthetic fixture result separate from the human review
requirement in plan sections 10.1 and 10.2.  A template or a partially filled
annotation file can therefore produce useful evidence while never making the
human-comparison gate pass.

Run from the repository root::

    backend/.venv/Scripts/python.exe evals/phase6_completion.py \
        --output evals/artifacts/phase6-completion-matrix.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

try:
    from evals.annotations.annotation_tool import (
        STRATEGIES,
        ValidationReport,
        load_annotations,
        validate_dataset,
    )
except ModuleNotFoundError as exc:  # direct ``python evals/phase6_completion.py`` execution
    if exc.name not in {"evals", "evals.annotations", "evals.annotations.annotation_tool"}:
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals.annotations.annotation_tool import (
        STRATEGIES,
        ValidationReport,
        load_annotations,
        validate_dataset,
    )


MATRIX_VERSION = "phase6-completion-v1"
DEFAULT_OFFLINE_REPORT = Path("evals/artifacts/phase6-evaluation.json")
DEFAULT_ANNOTATIONS = Path("evals/annotations/phase6-human-labels.template.json")
REQUIRED_DELIVERABLES = (
    Path("evals/visibility_eval.py"),
    Path("evals/annotations/annotation_tool.py"),
    Path("evals/annotations/phase6-human-labels.template.json"),
    Path("evals/annotations/README.md"),
    Path("evals/tests/test_annotation_tool.py"),
    Path("docs/phase-6-report.md"),
)


def _check(name: str, complete: bool, evidence: list[str], gaps: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "complete": complete,
        "evidence": evidence,
        "gaps": gaps,
    }


def _read_json(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unable to read {path}: {exc}"
    if not isinstance(value, Mapping):
        return None, f"{path} root must be a JSON object"
    return value, None


def _offline_check(path: Path, *, evidence_path: str | None = None) -> dict[str, Any]:
    report, error = _read_json(path)
    if error:
        return _check("10.1 offline fixture regression", False, [], [error])
    assert report is not None
    evidence = [evidence_path or str(path)]
    gaps: list[str] = []
    required_top_level = {
        "dataset_version": report.get("dataset_version"),
        "model_version": report.get("model_version"),
        "prompt_version": report.get("prompt_version"),
    }
    for key, value in required_top_level.items():
        if not isinstance(value, str) or not value.strip():
            gaps.append(f"missing {key}")

    expected_counts = {"sample_count": 40, "dev_count": 30, "holdout_count": 10}
    for key, expected in expected_counts.items():
        if report.get(key) != expected:
            gaps.append(f"{key} must be {expected}, found {report.get(key)!r}")

    results = report.get("results")
    if not isinstance(results, list) or len(results) != 40 * len(STRATEGIES):
        gaps.append(f"results must contain {40 * len(STRATEGIES)} strategy rows")
    strategies = report.get("strategies")
    if not isinstance(strategies, Mapping):
        gaps.append("strategies summary is missing")
    else:
        for strategy in STRATEGIES:
            summary = strategies.get(strategy)
            if not isinstance(summary, Mapping):
                gaps.append(f"missing strategy summary: {strategy}")
                continue
            if summary.get("sample_count") != 40:
                gaps.append(f"{strategy}.sample_count must be 40")
            if summary.get("holdout_count") != 10:
                gaps.append(f"{strategy}.holdout_count must be 10")
            latency = summary.get("latency_ms")
            if not isinstance(latency, Mapping) or not all(
                isinstance(latency.get(key), (int, float)) and latency.get(key) >= 0
                for key in ("p50", "p95")
            ):
                gaps.append(f"{strategy} is missing non-negative p50/p95 latency")
            cost = summary.get("cost_usd")
            if not isinstance(cost, Mapping) or cost.get("basis") != "fixture_zero_cost":
                gaps.append(f"{strategy} must declare fixture_zero_cost")

    complete = not gaps
    if complete:
        evidence.extend(
            [
                "40 synthetic cases: 30 dev and 10 holdout",
                "all three strategies have sample, holdout, latency, and fixture-cost fields",
            ]
        )
    return _check("10.1 offline fixture regression", complete, evidence, gaps)


def _human_check(
    path: Path,
    *,
    evidence_path: str | None = None,
) -> tuple[dict[str, Any], ValidationReport | None]:
    try:
        data = load_annotations(path)
    except (OSError, ValueError) as exc:
        return _check("10.1 human annotation set", False, [], [str(exc)]), None
    report = validate_dataset(data)
    evidence = [evidence_path or str(path), f"{report.complete_case_count} complete case rows"]
    gaps: list[str] = []
    if not report.schema_valid:
        gaps.append(f"schema errors: {len(report.errors)}")
    if not report.complete:
        gaps.append(
            f"annotation completion gate is false ({report.complete_case_count}/30-50 complete rows)"
        )
        if report.incomplete:
            gaps.append(f"{len(report.incomplete)} required fields or freeze checks are incomplete")
    complete = report.schema_valid and report.complete
    if complete:
        evidence.append("all required labels, decisions, provenance, strategies, and frozen holdout passed")
    return _check("10.1 human annotation set", complete, evidence, gaps), report


def _comparison_check(annotation_check: Mapping[str, Any], report: ValidationReport | None) -> dict[str, Any]:
    evidence = ["same annotation validator and frozen page/question metadata used for the comparison"]
    gaps: list[str] = []
    if report is None or not annotation_check.get("complete"):
        gaps.append("10.2 cannot pass until an independent complete annotation set exists")
    else:
        evidence.append("three strategy rows and four blind-review dimensions are complete")
    return _check("10.2 content strategy comparison", not gaps, evidence, gaps)


def _deliverables_check(root: Path) -> dict[str, Any]:
    missing = [str(path) for path in REQUIRED_DELIVERABLES if not (root / path).is_file()]
    evidence = [path.as_posix() for path in REQUIRED_DELIVERABLES if (root / path).is_file()]
    return _check(
        "Phase 6 evaluation deliverables",
        not missing,
        evidence,
        [f"missing deliverable: {Path(path).as_posix()}" for path in missing],
    )


def build_matrix(
    *,
    offline_report: Path = DEFAULT_OFFLINE_REPORT,
    annotations: Path = DEFAULT_ANNOTATIONS,
    root: Path | None = None,
) -> dict[str, Any]:
    """Build a truthful completion matrix from current artifacts."""

    root = root or Path.cwd()
    offline_path = offline_report if offline_report.is_absolute() else root / offline_report
    annotation_path = annotations if annotations.is_absolute() else root / annotations

    def evidence_path(path: Path) -> str:
        """Keep generated evidence portable across checkout locations."""

        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return str(path)

    offline = _offline_check(offline_path, evidence_path=evidence_path(offline_path))
    human, validation = _human_check(annotation_path, evidence_path=evidence_path(annotation_path))
    comparison = _comparison_check(human, validation)
    deliverables = _deliverables_check(root)
    checks = {
        "offline_fixture": offline,
        "human_annotations": human,
        "content_strategy_comparison": comparison,
        "deliverables": deliverables,
    }
    return {
        "matrix_version": MATRIX_VERSION,
        "overall_complete": all(check["complete"] for check in checks.values()),
        "human_evaluation_claim_allowed": human["complete"],
        "adoption_rate_claim_allowed": human["complete"],
        "checks": checks,
        "interpretation": (
            "Synthetic fixture evidence and human-review completion are separate gates. "
            "A false human_annotations check forbids human quality or adoption claims."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-report", type=Path, default=DEFAULT_OFFLINE_REPORT)
    parser.add_argument("--annotations", type=Path, default=DEFAULT_ANNOTATIONS)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root for deliverable checks")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="write the matrix and return zero even when a completion gate is false",
    )
    args = parser.parse_args(argv)
    matrix = build_matrix(offline_report=args.offline_report, annotations=args.annotations, root=args.root)
    encoded = json.dumps(matrix, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0 if matrix["overall_complete"] or args.allow_incomplete else 1


if __name__ == "__main__":
    raise SystemExit(main())
