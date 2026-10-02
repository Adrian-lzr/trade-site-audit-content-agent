"""Generate and validate the deterministic synthetic T15 evaluation manifest.

This fixture is intentionally separate from human annotation records.  It contains
case expectations, but no model output, human label, score, or business result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_VERSION = "t15-synthetic-v1"
EXPECTED_CASE_COUNT = 50
EXPECTED_SPLITS = {"dev": 20, "holdout": 30}
_GROUPS = (
    ("dev", "valve-alpha", "source-alpha-spec-v1"),
    ("dev", "valve-beta", "source-beta-spec-v1"),
    ("dev", "valve-gamma", "source-gamma-spec-v1"),
    ("dev", "valve-delta", "source-delta-spec-v1"),
    ("holdout", "valve-epsilon", "source-epsilon-spec-v1"),
    ("holdout", "valve-zeta", "source-zeta-spec-v1"),
    ("holdout", "valve-eta", "source-eta-spec-v1"),
    ("holdout", "valve-theta", "source-theta-spec-v1"),
    ("holdout", "valve-iota", "source-iota-spec-v1"),
    ("holdout", "valve-kappa", "source-kappa-spec-v1"),
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _facts(product: str, index: int) -> list[dict[str, Any]]:
    return [
        {
            "fact_id": f"{product}-f-pressure",
            "predicate": "pressure_rating",
            "value": "16",
            "unit": "bar",
            "product_id": product,
            "fact_version": 1,
            "scope": {"market": "US", "model": product},
        },
        {
            "fact_id": f"{product}-f-min-order",
            "predicate": "minimum_order_quantity",
            "value": str(10 + index),
            "unit": "pieces",
            "product_id": product,
            "fact_version": 1,
            "scope": {"market": "US", "model": product},
        },
    ]


def _case(group_split: str, product: str, source: str, offset: int) -> dict[str, Any]:
    case_id = f"t15-{group_split}-{offset:03d}"
    raw_descriptor = f"synthetic-raw-document|{source}|{product}|fixture-v1"
    source_hash = _sha256(raw_descriptor)
    facts = _facts(product, offset)
    mode = offset % 5
    if mode == 0:
        question = f"What pressure rating and minimum order apply to {product}?"
        expected = {
            "kind": "answer",
            "answer": f"{product} is rated to 16 bar with a minimum order of {10 + offset} pieces.",
            "refusal_reason": None,
        }
    elif mode == 1:
        question = f"Can {product} be specified as 1,600 kPa?"
        expected = {
            "kind": "answer",
            "answer": f"Yes. The 16 bar rating is equivalent to 1,600 kPa for {product}.",
            "refusal_reason": None,
        }
    elif mode == 2:
        question = f"What is the lead time for {product}?"
        expected = {
            "kind": "refusal",
            "answer": None,
            "refusal_reason": "lead_time is not present in the confirmed synthetic source facts",
        }
    elif mode == 3:
        question = f"Does superseded version 0 of {product} still define the pressure rating?"
        expected = {
            "kind": "refusal",
            "answer": None,
            "refusal_reason": "the requested fact version is superseded and cannot support a current answer",
        }
    else:
        question = f"Can the pressure rating for {product} be applied to valve-OTHER?"
        expected = {
            "kind": "refusal",
            "answer": None,
            "refusal_reason": "the confirmed fact is scoped to this product and cannot be transferred to another model",
        }
    return {
        "case_id": case_id,
        "synthetic": True,
        "split": group_split,
        "group": {"product_id": product, "source_id": source},
        "source": {
            "raw_source_sha256": source_hash,
            "source_locator": f"fixture://t15/{source}.html",
            "source_format": "synthetic_html_snapshot",
            "raw_content_status": "synthetic_descriptor_only",
        },
        "question": question,
        "required_facts": facts,
        "expected": expected,
        "holdout_access_policy": {
            "policy_version": "t15-holdout-access-v1",
            "expected_value_access": "evaluator_only" if group_split == "holdout" else "development",
            "permitted_before_evaluation": ["schema_validation", "count_validation", "hash_inventory"]
            if group_split == "holdout"
            else ["all_fixture_operations"],
            "human_annotation": "not_started",
        },
    }


def generate_manifest() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    offset = 0
    for split, product, source in _GROUPS:
        for _ in range(5):
            cases.append(_case(split, product, source, offset))
            offset += 1
    return {
        "manifest_version": MANIFEST_VERSION,
        "dataset_status": "synthetic_fixture_only",
        "synthetic_notice": "All products, sources, facts, questions, and expectations are synthetic; no human or business result is recorded.",
        "created_at": "2026-10-02T00:00:00+00:00",
        "source_hash_algorithm": "sha256",
        "split_counts": dict(EXPECTED_SPLITS),
        "grouping_policy": "Each product/source group belongs to exactly one split; no group is shared by dev and holdout.",
        "holdout_access_policy": {
            "policy_version": "t15-holdout-access-v1",
            "status": "frozen",
            "development_access": "metadata_only",
            "expected_value_access": "evaluator_only",
            "raw_source_access": "evaluator_only",
            "human_review_status": "not_started",
            "scores_present": False,
            "enforcement": "protocol_and_validator; production holdout storage must use an access-controlled store",
        },
        "evaluation_protocol": {
            "strategies": ["original", "ordinary_rewrite", "fact_constrained_rewrite"],
            "model_outputs_present": False,
            "human_annotations_present": False,
            "real_provider_results_present": False,
        },
        "dev_case_ids": [case["case_id"] for case in cases if case["split"] == "dev"],
        "holdout_case_ids": [case["case_id"] for case in cases if case["split"] == "holdout"],
        "holdout_frozen_at": "2026-10-02T00:00:00+00:00",
        "cases": cases,
    }


def validate_manifest(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases = data.get("cases")
    if data.get("manifest_version") != MANIFEST_VERSION:
        errors.append("manifest_version must be t15-synthetic-v1")
    if data.get("dataset_status") != "synthetic_fixture_only":
        errors.append("dataset_status must identify a synthetic fixture")
    if not isinstance(cases, list) or len(cases) != EXPECTED_CASE_COUNT:
        errors.append("cases must contain exactly 50 entries")
        return errors
    ids = [case.get("case_id") for case in cases if isinstance(case, dict)]
    if len(ids) != len(set(ids)):
        errors.append("case_id values must be unique")
    counts = {split: sum(case.get("split") == split for case in cases) for split in EXPECTED_SPLITS}
    if counts != EXPECTED_SPLITS:
        errors.append(f"split counts must be {EXPECTED_SPLITS}, got {counts}")
    groups: dict[str, set[str]] = {}
    for index, case in enumerate(cases):
        path = f"cases[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{path} must be an object")
            continue
        if case.get("synthetic") is not True:
            errors.append(f"{path}.synthetic must be true")
        split = case.get("split")
        group = case.get("group")
        source = case.get("source")
        if split not in EXPECTED_SPLITS or not isinstance(group, dict) or not isinstance(source, dict):
            errors.append(f"{path} has invalid split/group/source")
            continue
        group_key = f"{group.get('product_id')}|{group.get('source_id')}"
        groups.setdefault(group_key, set()).add(split)
        raw_hash = source.get("raw_source_sha256")
        if not isinstance(raw_hash, str) or len(raw_hash) != 64 or any(char not in "0123456789abcdef" for char in raw_hash):
            errors.append(f"{path}.source.raw_source_sha256 must be lowercase SHA-256")
        if not isinstance(case.get("question"), str) or not case["question"].strip():
            errors.append(f"{path}.question is required")
        if not isinstance(case.get("required_facts"), list) or not case["required_facts"]:
            errors.append(f"{path}.required_facts must be non-empty")
        expected = case.get("expected")
        if not isinstance(expected, dict) or expected.get("kind") not in {"answer", "refusal"}:
            errors.append(f"{path}.expected must declare answer or refusal")
        elif expected["kind"] == "answer" and (not expected.get("answer") or expected.get("refusal_reason") is not None):
            errors.append(f"{path}.expected answer shape is invalid")
        elif expected["kind"] == "refusal" and (expected.get("answer") is not None or not expected.get("refusal_reason")):
            errors.append(f"{path}.expected refusal shape is invalid")
        policy = case.get("holdout_access_policy")
        if not isinstance(policy, dict) or policy.get("policy_version") != "t15-holdout-access-v1":
            errors.append(f"{path}.holdout_access_policy is required")
    for key, splits in groups.items():
        if len(splits) != 1:
            errors.append(f"group {key} is shared by dev and holdout")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write a generated manifest")
    parser.add_argument("--validate", type=Path, help="validate an existing manifest")
    args = parser.parse_args()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(generate_manifest(), indent=2) + "\n", encoding="utf-8")
    data = json.loads(args.validate.read_text(encoding="utf-8")) if args.validate else generate_manifest()
    errors = validate_manifest(data)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"valid synthetic T15 manifest: {len(data['cases'])} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
