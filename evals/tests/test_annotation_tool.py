from __future__ import annotations

from copy import deepcopy

from evals.annotations.annotation_tool import (
    DECISIONS,
    LABEL_FIELDS,
    STRATEGIES,
    summarize_dataset,
    validate_dataset,
)


def _case(index: int, *, complete: bool = True) -> dict[str, object]:
    strategy = STRATEGIES[index % len(STRATEGIES)]
    decision = DECISIONS[(index // len(STRATEGIES)) % len(DECISIONS)]
    row: dict[str, object] = {
        "case_id": f"case-{index:02d}",
        "strategy": strategy,
        "split": "holdout" if index > 27 else "dev",
        "page_id": f"page-{index % 5 + 1}",
        "buyer_question_id": f"question-{index % 20 + 1:02d}",
        "source_snapshot": {
            "html_sha256": f"{index:064x}",
            "facts_sha256": f"{index + 100:064x}",
            "source_locator": f"fixture://case-{index:02d}",
        },
        "annotator": "reviewer-a",
        "annotated_at": "2026-10-01T08:00:00+08:00",
        "labels": {field: 4 for field in LABEL_FIELDS},
        "decision": decision,
        "decision_reason": "Changed the opening sentence." if decision != "adopted" else "",
        "notes": "reviewed independently",
    }
    if not complete:
        row["labels"] = {
            "factual_consistency": None,
            "procurement_question_coverage": 4,
            "readability": 4,
            "actionability": 4,
        }
        row["decision"] = None
        row["decision_reason"] = ""
    return row


def _dataset(*, incomplete_index: int | None = None) -> dict[str, object]:
    cases = [_case(index, complete=index != incomplete_index) for index in range(1, 31)]
    return {
        "dataset_version": "phase6-human-labels-test-v1",
        "annotation_status": "complete" if incomplete_index is None else "in_progress",
        "label_schema_version": "1.1",
        "holdout_policy": "Freeze before comparison",
        "holdout_frozen_at": "2026-10-01T07:00:00+08:00",
        "holdout_case_ids": [f"case-{index:02d}" for index in range(28, 31)],
        "strategies": list(STRATEGIES),
        "comparison": {
            "page_ids": ["page-1"],
            "question_ids": [f"question-{index:02d}" for index in range(1, 21)],
            "question_set_version": "questions-v1",
            "model_version": "human-review-fixture",
            "prompt_version": "prompt-v1",
        },
        "cases": cases,
    }


def test_template_is_incomplete_and_empty_labels_are_not_scores():
    from pathlib import Path
    import json

    template = json.loads(
        Path("evals/annotations/phase6-human-labels.template.json").read_text(encoding="utf-8")
    )
    report = validate_dataset(template)
    summary = summarize_dataset(template)

    assert report.schema_valid
    assert not report.complete
    assert report.complete_case_count == 0
    assert summary["complete"] is False
    assert summary["complete_case_count"] == 0
    assert summary["strategies"]["original"]["label_means"]["readability"] is None
    assert summary["decision_counts"] == {decision: 0 for decision in DECISIONS}


def test_complete_dataset_summarizes_each_strategy_and_decision():
    data = _dataset()
    report = validate_dataset(data)
    summary = summarize_dataset(data)

    assert report.schema_valid
    assert report.complete
    assert report.complete_case_count == 30
    assert summary["complete"] is True
    assert summary["sample_count"] == 30
    assert summary["complete_case_count"] == 30
    assert sum(summary["decision_counts"].values()) == 30
    for strategy in STRATEGIES:
        strategy_summary = summary["strategies"][strategy]
        assert strategy_summary["complete_case_count"] == 10
        assert strategy_summary["incomplete_case_count"] == 0
        assert strategy_summary["label_means"]["factual_consistency"] == 4.0
        assert strategy_summary["label_4_or_5_rates"]["actionability"] == 1.0


def test_incomplete_case_is_excluded_from_metrics_and_completion():
    data = _dataset(incomplete_index=1)
    report = validate_dataset(data)
    summary = summarize_dataset(data)

    assert report.schema_valid
    assert not report.complete
    assert report.complete_case_count == 29
    assert summary["complete_case_count"] == 29
    # Case 1 is the ordinary strategy and is not silently scored as zero.
    assert summary["strategies"]["ordinary_rewrite"]["complete_case_count"] == 9
    assert summary["strategies"]["ordinary_rewrite"]["label_means"]["readability"] == 4.0
    assert summary["strategies"]["original"]["decision_counts"] == {
        "adopted": 3,
        "adopted_after_edit": 4,
        "rejected": 3,
    }


def test_invalid_values_and_duplicate_ids_are_schema_errors():
    data = _dataset()
    data["cases"] = deepcopy(data["cases"])
    data["cases"][1]["case_id"] = data["cases"][0]["case_id"]
    data["cases"][2]["labels"]["readability"] = 6
    data["cases"][3]["decision"] = "maybe"

    report = validate_dataset(data)

    assert not report.schema_valid
    assert any("duplicate case_id" in issue.message for issue in report.errors)
    assert any("integer from 1 to 5" in issue.message for issue in report.errors)
    assert any("must be one of" in issue.message for issue in report.errors)
    assert not report.complete
