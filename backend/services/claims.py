"""Deterministic checks for high-risk content claims.

This module accepts ORM facts, workflow facts, or simple mappings.  It never
trusts claim fields supplied by a model; the authoritative fact row must match
the bound identity and value before a revision can proceed.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any


_NUMBER_WITH_UNIT = re.compile(
    r"(?<![A-Za-z])(?P<number>\d+(?:[.,]\d+)?)\s*(?P<unit>%|bar|psi|kpa|mpa|°?c|°?f|pieces?|pcs?|units?|days?|weeks?|months?|kg|g|mm|cm|m|in|inch(?:es)?)?\b",
    re.IGNORECASE,
)
_CERTIFICATION = re.compile(r"\b(fda|ce|iso\s*\d{3,5}|rohs|reach)\b[^.!?\n]{0,40}\b(approved|certified|compliant|certification|certificate)\b", re.IGNORECASE)


def _get(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _number(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None


def _unit_category(unit: str | None) -> str | None:
    if not unit:
        return None
    value = unit.casefold().strip().replace("°", "")
    if value in {"%", "percent", "percentage"}:
        return "percentage"
    if value in {"bar", "psi", "kpa", "mpa"}:
        return "pressure"
    if value in {"c", "f", "celcius", "celsius", "fahrenheit"}:
        return "temperature"
    if value in {"piece", "pieces", "pcs", "unit", "units"}:
        return "quantity"
    if value in {"day", "days", "week", "weeks", "month", "months"}:
        return "duration"
    return value


def validate_claim_bindings(claims: Sequence[Mapping[str, Any]], facts: Sequence[Any]) -> list[str]:
    """Validate every explicit claim against an authoritative fact row."""
    by_id = {int(_get(fact, "id")): fact for fact in facts if _get(fact, "id") is not None}
    issues: list[str] = []
    seen: set[str] = set()
    for claim in claims:
        claim_id = str(claim.get("claim_id", ""))
        if not claim_id or claim_id in seen:
            issues.append("claim_id must be unique and non-empty")
            continue
        seen.add(claim_id)
        try:
            fact = by_id[int(claim["fact_id"])]
        except (KeyError, TypeError, ValueError):
            issues.append(f"{claim_id} is bound to an unavailable fact")
            continue
        for field in ("version", "series_id", "subject", "predicate", "value", "unit"):
            expected = _get(fact, field)
            supplied = claim.get("fact_version" if field == "version" else field)
            if supplied is not None and str(supplied).strip() != str(expected or "").strip():
                issues.append(f"{claim_id} does not match the authoritative fact {field}")
                break
        if not str(claim.get("source_locator", "")).strip():
            issues.append(f"{claim_id} is missing source_locator")
    return issues[:10]


def validate_high_risk_claims(field_diff: Mapping[str, Any], facts: Sequence[Any]) -> list[str]:
    """Find unsupported certification and unit/category expansions in text."""
    texts = _strings_in(field_diff)
    normalized_facts = list(facts)
    issues: list[str] = []
    for text in texts:
        for match in _CERTIFICATION.finditer(text):
            term = match.group(1).casefold().replace(" ", "")
            supported = any(
                term in str(_get(fact, "predicate", "")).casefold().replace(" ", "")
                or term in str(_get(fact, "value", "")).casefold().replace(" ", "")
                for fact in normalized_facts
            )
            if not supported:
                issues.append(f"unsupported certification claim: {match.group(0).strip()}")
    for text in texts:
        for match in _NUMBER_WITH_UNIT.finditer(text):
            text_number = _number(match.group("number"))
            text_category = _unit_category(match.group("unit"))
            if text_number is None or text_category is None:
                continue
            matching_values = []
            for fact in normalized_facts:
                fact_number = _number(str(_get(fact, "value", "")))
                if fact_number is not None and fact_number == text_number:
                    matching_values.append(_unit_category(_get(fact, "unit")))
            if matching_values and text_category not in matching_values:
                issues.append(f"numeric claim uses an incompatible unit category: {match.group(0).strip()}")
    return issues[:10]


def _strings_in(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [text for nested in value.values() for text in _strings_in(nested)]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [text for nested in value for text in _strings_in(nested)]
    return []
