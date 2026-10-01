from __future__ import annotations

import json
from pathlib import Path

from evals.phase6_completion import STRATEGIES, _offline_check, build_matrix


ROOT = Path(__file__).resolve().parents[2]


def _offline_fixture_report() -> dict[str, object]:
    return {
        "dataset_version": "phase6-frozen-test-v1",
        "model_version": "fixture-test-v1",
        "prompt_version": "prompt-test-v1",
        "sample_count": 40,
        "dev_count": 30,
        "holdout_count": 10,
        "strategies": {
            strategy: {
                "sample_count": 40,
                "holdout_count": 10,
                "latency_ms": {"p50": 1.0, "p95": 2.0},
                "cost_usd": {"basis": "fixture_zero_cost"},
            }
            for strategy in STRATEGIES
        },
        "results": [{} for _ in range(40 * len(STRATEGIES))],
    }


def test_current_matrix_keeps_human_claims_disabled_for_template():
    matrix = build_matrix(root=ROOT)

    assert matrix["checks"]["offline_fixture"]["complete"] is True
    assert matrix["checks"]["human_annotations"]["complete"] is False
    assert matrix["checks"]["content_strategy_comparison"]["complete"] is False
    assert matrix["human_evaluation_claim_allowed"] is False
    assert matrix["adoption_rate_claim_allowed"] is False
    assert matrix["overall_complete"] is False

    evidence = [
        item
        for check in matrix["checks"].values()
        for item in check["evidence"]
        if isinstance(item, str)
    ]
    assert str(ROOT) not in "\n".join(evidence)
    assert "evals/artifacts/phase6-evaluation.json" in evidence
    assert "evals/annotations/phase6-human-labels.template.json" in evidence


def test_offline_check_requires_all_fixture_counts_and_strategy_fields(tmp_path: Path):
    report_path = tmp_path / "offline.json"
    report_path.write_text(json.dumps(_offline_fixture_report()), encoding="utf-8")
    assert _offline_check(report_path)["complete"] is True

    broken = _offline_fixture_report()
    broken["holdout_count"] = 9
    broken["strategies"][STRATEGIES[0]]["cost_usd"]["basis"] = "live_unknown"
    report_path.write_text(json.dumps(broken), encoding="utf-8")
    result = _offline_check(report_path)

    assert result["complete"] is False
    assert any("holdout_count must be 10" in gap for gap in result["gaps"])
    assert any("fixture_zero_cost" in gap for gap in result["gaps"])


def test_missing_annotation_file_cannot_pass_human_gate(tmp_path: Path):
    matrix = build_matrix(
        offline_report=tmp_path / "missing-offline.json",
        annotations=tmp_path / "missing-annotations.json",
        root=ROOT,
    )

    assert matrix["checks"]["offline_fixture"]["complete"] is False
    assert matrix["checks"]["human_annotations"]["complete"] is False
    assert matrix["human_evaluation_claim_allowed"] is False
    assert matrix["overall_complete"] is False
