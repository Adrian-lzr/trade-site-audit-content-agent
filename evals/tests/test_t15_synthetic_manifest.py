from __future__ import annotations

import json
from pathlib import Path

from evals.optimization_t15_manifest import validate_manifest


MANIFEST = Path("evals/artifacts/optimization-t15-synthetic-v1.json")


def _load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_t15_manifest_has_frozen_synthetic_split_and_group_isolation():
    data = _load()
    assert validate_manifest(data) == []
    assert data["split_counts"] == {"dev": 20, "holdout": 30}
    assert len(data["cases"]) == 50
    assert all(case["synthetic"] is True for case in data["cases"])
    groups = {
        (case["group"]["product_id"], case["group"]["source_id"]): case["split"]
        for case in data["cases"]
    }
    assert len(groups) == 10
    for key in groups:
        assert sum(
            1
            for case in data["cases"]
            if (case["group"]["product_id"], case["group"]["source_id"]) == key
        ) == 5


def test_t15_cases_have_hash_facts_and_explicit_answer_or_refusal_without_scores():
    data = _load()
    ids = [case["case_id"] for case in data["cases"]]
    assert len(ids) == len(set(ids))
    for case in data["cases"]:
        assert len(case["source"]["raw_source_sha256"]) == 64
        assert case["required_facts"]
        expected = case["expected"]
        assert expected["kind"] in {"answer", "refusal"}
        if expected["kind"] == "answer":
            assert expected["answer"] and expected["refusal_reason"] is None
        else:
            assert expected["answer"] is None and expected["refusal_reason"]
        assert case["holdout_access_policy"]["human_annotation"] == "not_started"
    assert data["evaluation_protocol"]["human_annotations_present"] is False
    assert data["evaluation_protocol"]["model_outputs_present"] is False


def test_t15_validator_rejects_duplicate_ids_and_cross_split_group():
    data = _load()
    data["cases"][1]["case_id"] = data["cases"][0]["case_id"]
    data["cases"][1]["split"] = "holdout"
    data["cases"][1]["group"] = data["cases"][0]["group"]
    errors = validate_manifest(data)
    assert any("case_id values must be unique" in error for error in errors)
    assert any("shared by dev and holdout" in error for error in errors)
