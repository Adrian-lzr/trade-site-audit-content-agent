"""Pure, versioned visibility metric calculation.

The service accepts mappings, dataclasses, or ORM-like objects and performs no
database or network access. Citation records from a Provider are treated as
native by default; links discovered in answer text are retained as inferred
evidence and never contribute to the validated site-citation metric.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit


METRIC_VERSION = "visibility-metrics-v2"
_SUCCESS_STATUSES = {"success", "succeeded", "complete", "completed"}
_FAILURE_STATUSES = {"failed", "error"}
_UNAVAILABLE_STATUSES = {"unavailable", "not_available"}
_PENDING_STATUSES = {"queued", "pending", "running", "in_progress"}
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}"


def calculate_visibility_metrics(
    samples: Iterable[Any],
    *,
    site_url: str,
    brand_terms: Iterable[str] = (),
    planned_samples: int | None = None,
    expected_questions: Iterable[Any] | None = None,
    allowed_site_hosts: Iterable[str] | None = None,
    allow_subdomains: bool = True,
    is_synthetic: bool = False,
    provider_kind: str | None = None,
) -> dict[str, Any]:
    """Calculate versioned visibility metrics from sample evidence.

    ``answered_question`` is a nullable independent-evaluator result. A missing
    value remains unknown; answer text, mentions, and citations never substitute
    for this evidence. Question-level metrics deduplicate by stable question ID.

    Metric rates have explicit numerators and denominators. Mention and citation
    rates use distinct successful question IDs whose corresponding evidence was
    captured. Sample success uses planned sample attempts, including attempts
    with no recorded row. Missing sample cost is reported as unknown, not zero.
    """

    source_samples = list(samples)
    prepared = [_prepare_sample(sample, index, is_synthetic, provider_kind) for index, sample in enumerate(source_samples)]
    reported_plan = max(0, int(planned_samples or 0))
    sample_denominator = max(reported_plan, len(prepared))
    terms = _clean_terms(brand_terms)
    site_hosts = _configured_site_hosts(site_url, allowed_site_hosts)

    for item in prepared:
        item["citations"] = _citation_evidence(
            item["citations_input"],
            sample=item,
            answer_text=item["answer_text"],
            site_hosts=site_hosts,
            allow_subdomains=allow_subdomains,
        )
        item["validated_site_citation"] = any(
            citation["validated_site_citation"] for citation in item["citations"]
        )
        item["brand_mention"] = _contains_any(item["answer_text"], terms)
        item["domain_mention"] = _mentions_site_domain(
            item["answer_text"], item["mentioned_domains"], site_hosts, allow_subdomains
        )

    sample_counts = _sample_counts(prepared, reported_plan)
    observed_questions = _group_questions(prepared)
    expected_keys = _expected_question_keys(expected_questions)
    if expected_keys is None:
        question_keys = set(observed_questions)
    else:
        question_keys = expected_keys
    evaluated, answered, question_unknown, question_conflicts = _question_evaluation_counts(
        question_keys, observed_questions
    )

    brand_counts = _question_evidence_counts(question_keys, observed_questions, "brand_mention", require_answer=True)
    domain_counts = _question_evidence_counts(question_keys, observed_questions, "domain_mention", require_answer_or_domains=True)
    citation_counts = _question_evidence_counts(question_keys, observed_questions, "validated_site_citation", require_citations=True)

    cost = _cost_summary(prepared, sample_denominator)
    strata = _strata_summary(prepared, reported_plan)
    source_categories = sorted(strata)
    return {
        "metric_version": METRIC_VERSION,
        "metric_population": "all supplied samples; compare source strata separately when evidence_strata.mixed is true",
        "site_host": site_hosts[0] if site_hosts else None,
        "site_host_valid": bool(site_hosts),
        "subdomains_allowed": bool(allow_subdomains),
        "sample_counts": sample_counts,
        "sample_success": _metric(sample_counts["succeeded"], sample_denominator),
        "brand_mention": _metric(brand_counts["numerator"], brand_counts["denominator"]),
        "domain_mention": _metric(domain_counts["numerator"], domain_counts["denominator"]),
        "validated_site_citation": _metric(citation_counts["numerator"], citation_counts["denominator"]),
        "answered_question": {
            **_metric(answered, evaluated),
            "unique_question_count": len(question_keys),
            "evaluated_question_count": evaluated,
            "unknown_question_count": question_unknown,
            "conflicting_evaluation_count": question_conflicts,
            "evaluation_coverage": _metric(evaluated, len(question_keys)),
            "basis": "independent_evaluator_boolean_only",
        },
        "question_counts": {
            "unique_question_count": len(question_keys),
            "observed_question_count": len(observed_questions),
            "unobserved_question_count": len(question_keys - set(observed_questions)),
            "unidentified_sample_count": sum(item["question_key"] is None for item in prepared),
        },
        "mention_details": {
            "brand_mention_count": brand_counts["numerator"],
            "brand_mention_denominator": brand_counts["denominator"],
            "domain_mention_count": domain_counts["numerator"],
            "domain_mention_denominator": domain_counts["denominator"],
            "domain_mention_sources": ["answer_text", "mentioned_domains"],
            "brand_terms": terms,
        },
        "citation_details": {
            "validated_site_citation_count": citation_counts["numerator"],
            "citation_question_denominator": citation_counts["denominator"],
            "native_site_citation_count": sum(
                citation["validated_site_citation"]
                for item in prepared
                for citation in item["citations"]
            ),
            "inferred_site_link_count": sum(
                citation["kind"] == "inferred" and citation["validation_status"] == "site_match"
                for item in prepared
                for citation in item["citations"]
            ),
            "citation_evidence": [
                {
                    **citation,
                    "sample_index": item["sample_index"],
                    "question_id": item["question_id"],
                }
                for item in prepared
                for citation in item["citations"]
            ],
            "counting_rule": "only validated native citation records count; mentioned_domains, answer-text mentions, and inferred links do not",
        },
        "cost": cost,
        "evidence_strata": {
            "categories": source_categories,
            "mixed": len(source_categories) > 1,
            "by_category": strata,
            "synthetic_sample_count": sum(item["is_synthetic"] for item in prepared),
            "manual_sample_count": sum(item["is_manual"] for item in prepared),
            "consumer_search_surface_sample_count": sum(
                item["provider_kind"] == "consumer_search_surface" for item in prepared
            ),
            "production_evidence_note": "synthetic, manual, consumer-surface, and generic Provider observations remain separately identified",
        },
    }


def _prepare_sample(sample: Any, index: int, run_synthetic: bool, run_provider_kind: str | None) -> dict[str, Any]:
    question_id = _read(sample, "question_id")
    if question_id is not None and str(question_id).strip():
        question_key = str(question_id).strip()
    else:
        question_key = None

    question = _question_text(sample)
    status = str(_read(sample, "status", "unknown") or "unknown").strip().casefold()
    provider_kind = str(_read(sample, "provider_kind", None) or run_provider_kind or "").strip().casefold() or None
    synthetic = bool(_read(sample, "is_synthetic", run_synthetic))
    manual = bool(_read(sample, "is_manual", False)) or provider_kind == "manual_capture"
    if synthetic and manual:
        source_category = "synthetic_manual"
    elif synthetic:
        source_category = "synthetic"
    elif manual:
        source_category = "manual"
    elif provider_kind == "consumer_search_surface":
        source_category = "consumer_search_surface"
    elif provider_kind:
        source_category = "provider"
    else:
        source_category = "unknown"

    answer = _read(sample, "answer_text")
    answer_text = str(answer) if answer is not None else None
    mentioned_domains_value = _read_first(sample, "mentioned_domains", "mentioned_domains_json")
    mentioned_domains = _string_list(mentioned_domains_value)
    citations_value = _read_first(sample, "citations", "citations_json")
    return {
        "sample_index": index,
        "question_id": question_id,
        "question_key": question_key,
        "question": question,
        "status": status,
        "is_success": status in _SUCCESS_STATUSES,
        "is_synthetic": synthetic,
        "is_manual": manual,
        "provider_kind": provider_kind,
        "source_category": source_category,
        "answer_text": answer_text,
        "answer_available": answer is not None,
        "answered_question": _nullable_bool(_read(sample, "answered_question")),
        "mentioned_domains": mentioned_domains,
        "mentioned_domains_available": mentioned_domains_value is not _MISSING and mentioned_domains_value is not None,
        "citations_input": citations_value,
        "citations_available": citations_value is not _MISSING and citations_value is not None,
        "cost_usd": _read_first(sample, "cost_usd", "actual_cost_usd"),
        "estimated_cost_usd": _read_first(sample, "estimated_cost_usd", "estimated_cost"),
        "provider_source": _read_first(sample, "provider_raw_source", "provider", "provider_kind"),
        "answered_at": _timestamp(_read_first(sample, "answered_at", "completed_at")),
        "sample_crawled_at": _timestamp(_read_first(sample, "crawled_at", "fetched_at", "created_at")),
    }


def _sample_counts(samples: list[dict[str, Any]], reported_plan: int) -> dict[str, int | float | None]:
    statuses = [item["status"] for item in samples]
    succeeded = sum(status in _SUCCESS_STATUSES for status in statuses)
    failed = sum(status in _FAILURE_STATUSES for status in statuses)
    unavailable = sum(status in _UNAVAILABLE_STATUSES for status in statuses)
    pending = sum(status in _PENDING_STATUSES for status in statuses)
    known = succeeded + failed + unavailable + pending
    unknown_status = len(samples) - known
    denominator = max(reported_plan, len(samples))
    return {
        "reported_planned": reported_plan,
        "denominator": denominator,
        "observed": len(samples),
        "succeeded": succeeded,
        "failed": failed,
        "unavailable": unavailable,
        "pending": pending,
        "unknown_status": unknown_status,
        "unobserved": max(0, reported_plan - len(samples)),
        "unplanned_observed": max(0, len(samples) - reported_plan),
    }


def _group_questions(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in samples:
        if item["question_key"] is not None:
            groups[item["question_key"]].append(item)
    return dict(groups)


def _expected_question_keys(expected_questions: Iterable[Any] | None) -> set[str] | None:
    if expected_questions is None:
        return None
    keys: set[str] = set()
    for question in expected_questions:
        if isinstance(question, (str, int)):
            question_id = question
        else:
            question_id = _read(question, "question_id", _read(question, "id"))
        if question_id is not None and str(question_id).strip():
            keys.add(str(question_id).strip())
    return keys


def _question_evaluation_counts(
    question_keys: set[str], groups: dict[str, list[dict[str, Any]]]
) -> tuple[int, int, int, int]:
    evaluated = answered = unknown = conflicts = 0
    for question_key in question_keys:
        evidence = {
            item["answered_question"]
            for item in groups.get(question_key, [])
            if item["answered_question"] is not None
        }
        if not evidence:
            unknown += 1
        elif len(evidence) > 1:
            conflicts += 1
            unknown += 1
        else:
            evaluated += 1
            answered += next(iter(evidence)) is True
    return evaluated, answered, unknown, conflicts


def _question_evidence_counts(
    question_keys: set[str],
    groups: dict[str, list[dict[str, Any]]],
    evidence_field: str,
    *,
    require_answer: bool = False,
    require_answer_or_domains: bool = False,
    require_citations: bool = False,
) -> dict[str, int]:
    denominator = numerator = 0
    for question_key in question_keys:
        question_samples = [item for item in groups.get(question_key, []) if item["is_success"]]
        if require_answer:
            eligible = [item for item in question_samples if item["answer_available"]]
        elif require_answer_or_domains:
            eligible = [
                item
                for item in question_samples
                if item["answer_available"] or item["mentioned_domains_available"]
            ]
        elif require_citations:
            eligible = [item for item in question_samples if item["citations_available"]]
        else:
            eligible = question_samples
        if not eligible:
            continue
        denominator += 1
        numerator += any(bool(item[evidence_field]) for item in eligible)
    return {"numerator": numerator, "denominator": denominator}


def _cost_summary(samples: list[dict[str, Any]], denominator: int) -> dict[str, Any]:
    known_total = Decimal("0")
    estimated_total = Decimal("0")
    known_count = unknown_count = invalid_count = estimated_count = 0
    for sample in samples:
        raw_cost = sample["cost_usd"]
        raw_estimate = sample["estimated_cost_usd"]
        parsed_cost, cost_state = _parse_cost(raw_cost)
        parsed_estimate, estimate_state = _parse_cost(raw_estimate)
        if cost_state == "known":
            known_count += 1
            known_total += parsed_cost or Decimal("0")
        else:
            unknown_count += 1
            invalid_count += cost_state == "invalid"
        if estimate_state == "known":
            estimated_count += 1
            estimated_total += parsed_estimate or Decimal("0")
    unobserved = max(0, denominator - len(samples))
    return {
        "currency": "USD",
        "known_actual_cost_usd": _money(known_total),
        "known_actual_cost_sample_count": known_count,
        "unknown_actual_cost_sample_count": unknown_count,
        "invalid_actual_cost_sample_count": invalid_count,
        "estimated_cost_usd": _money(estimated_total),
        "estimated_cost_sample_count": estimated_count,
        "unobserved_sample_count": unobserved,
        "complete": unknown_count == 0 and unobserved == 0,
        "unknown": unknown_count > 0 or unobserved > 0,
        "unknown_cost_is_not_zero": True,
    }


def _strata_summary(samples: list[dict[str, Any]], reported_plan: int) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        groups[sample["source_category"]].append(sample)
    denominator = max(reported_plan, len(samples))
    output: dict[str, Any] = {}
    for category, group in sorted(groups.items()):
        counts = _sample_counts(group, len(group))
        question_groups = _group_questions(group)
        question_keys = set(question_groups)
        evaluated, answered, question_unknown, question_conflicts = _question_evaluation_counts(
            question_keys, question_groups
        )
        brand = _question_evidence_counts(question_keys, question_groups, "brand_mention", require_answer=True)
        domain = _question_evidence_counts(
            question_keys, question_groups, "domain_mention", require_answer_or_domains=True
        )
        citation = _question_evidence_counts(
            question_keys, question_groups, "validated_site_citation", require_citations=True
        )
        output[category] = {
            "sample_counts": counts,
            "sample_success": _metric(counts["succeeded"], counts["denominator"]),
            "brand_mention": _metric(brand["numerator"], brand["denominator"]),
            "domain_mention": _metric(domain["numerator"], domain["denominator"]),
            "validated_site_citation": _metric(citation["numerator"], citation["denominator"]),
            "answered_question": {
                **_metric(answered, evaluated),
                "unique_question_count": len(question_keys),
                "evaluated_question_count": evaluated,
                "unknown_question_count": question_unknown,
                "conflicting_evaluation_count": question_conflicts,
                "evaluation_coverage": _metric(evaluated, len(question_keys)),
                "basis": "independent_evaluator_boolean_only",
            },
            "cost": _cost_summary(group, len(group)),
            "provider_kinds": sorted({item["provider_kind"] for item in group if item["provider_kind"]}),
            "is_synthetic": any(item["is_synthetic"] for item in group),
            "is_manual": any(item["is_manual"] for item in group),
        }
    if not groups:
        return output
    # The run-level plan is only attributable to a stratum for a single-source run.
    if len(groups) == 1:
        only = next(iter(output.values()))
        only["sample_counts"]["denominator"] = denominator
        only["sample_counts"]["reported_planned"] = reported_plan
        only["sample_counts"]["unobserved"] = max(0, reported_plan - len(samples))
        only["sample_success"] = _metric(only["sample_counts"]["succeeded"], denominator)
    else:
        for values in output.values():
            values["sample_counts"]["run_level_unobserved_not_attributed"] = max(0, denominator - len(samples))
    return output


def _citation_evidence(
    citations_value: Any,
    *,
    sample: dict[str, Any],
    answer_text: str | None,
    site_hosts: list[str],
    allow_subdomains: bool,
) -> list[dict[str, Any]]:
    records = _citation_list(citations_value)
    normalized_records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for record in records:
        evidence = _normalize_citation(
            record,
            sample=sample,
            site_hosts=site_hosts,
            allow_subdomains=allow_subdomains,
            default_kind="native",
        )
        normalized_records.append(evidence)
        parsed_host = evidence["normalized_domain"]
        if parsed_host:
            seen_urls.add(evidence["url"].rstrip(_TRAILING_URL_PUNCTUATION).casefold())

    for candidate in _extract_urls(answer_text):
        if candidate.casefold() in seen_urls:
            continue
        normalized_records.append(
            _normalize_citation(
                {"url": candidate, "kind": "inferred", "provider_raw_source": "answer_text"},
                sample=sample,
                site_hosts=site_hosts,
                allow_subdomains=allow_subdomains,
                default_kind="inferred",
            )
        )
    return normalized_records


def _normalize_citation(
    record: Any,
    *,
    sample: dict[str, Any],
    site_hosts: list[str],
    allow_subdomains: bool,
    default_kind: str,
) -> dict[str, Any]:
    if isinstance(record, str):
        raw_url = record
        kind = default_kind
        raw_source = sample["provider_source"]
        crawled_at = sample["sample_crawled_at"]
        answered_at = sample["answered_at"]
    else:
        raw_url = _read(record, "url", _read(record, "href", ""))
        kind = str(_read(record, "kind", _read(record, "evidence_kind", default_kind)) or default_kind).casefold()
        if kind not in {"native", "inferred"}:
            kind = "unknown"
        raw_source = _read_first(record, "provider_raw_source", "raw_source", "source")
        if raw_source is _MISSING or raw_source is None:
            raw_source = sample["provider_source"]
        crawled_at = _timestamp(_read_first(record, "crawled_at", "fetched_at", "retrieved_at")) or sample["sample_crawled_at"]
        answered_at = _timestamp(_read(record, "answered_at")) or sample["answered_at"]
    url = str(raw_url or "").strip()
    normalized_domain, validation = _validate_citation_url(url, site_hosts, allow_subdomains)
    validated_site = kind == "native" and validation == "site_match"
    return {
        "url": url,
        "provider_raw_source": raw_source if raw_source is not _MISSING else None,
        "kind": kind,
        "normalized_domain": normalized_domain,
        "validation_status": validation,
        "validated_site_citation": validated_site,
        "crawled_at": crawled_at,
        "answered_at": answered_at,
    }


def _validate_citation_url(url: str, site_hosts: list[str], allow_subdomains: bool) -> tuple[str | None, str]:
    if not url or any(ord(char) < 32 or char.isspace() for char in url):
        return None, "invalid_url"
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.casefold()
        if scheme not in {"http", "https"}:
            return None, "unsupported_scheme"
        if not parts.netloc or parts.username is not None or parts.password is not None:
            return None, "invalid_url"
        _ = parts.port
        host = _normalize_host(parts.hostname or "")
    except (ValueError, UnicodeError):
        return None, "invalid_url"
    if not host:
        return None, "invalid_host"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return host, "internal_ip"
    if any(_host_matches(host, site_host, allow_subdomains) for site_host in site_hosts):
        return host, "site_match"
    return host, "other_domain"


def _configured_site_hosts(site_url: str, allowed_site_hosts: Iterable[str] | None) -> list[str]:
    inputs = [site_url]
    if allowed_site_hosts is not None:
        inputs.extend(allowed_site_hosts)
    hosts: list[str] = []
    for value in inputs:
        host = _normalize_host_from_url_or_host(str(value))
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def _normalize_host_from_url_or_host(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        if "://" in candidate:
            parts = urlsplit(candidate)
            host = parts.hostname or ""
            _ = parts.port
        else:
            parts = urlsplit("//" + candidate)
            host = parts.hostname or ""
            _ = parts.port
    except ValueError:
        return None
    return _normalize_host(host)


def _normalize_host(host: str) -> str | None:
    value = host.strip().rstrip(".").casefold()
    if not value or any(ord(char) < 33 for char in value):
        return None
    try:
        address = ipaddress.ip_address(value)
        return address.compressed.casefold()
    except ValueError:
        pass
    try:
        ascii_host = value.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if len(ascii_host) > 253:
        return None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in labels
    ):
        return None
    return ascii_host


def _host_matches(host: str, configured_host: str, allow_subdomains: bool) -> bool:
    if host == configured_host:
        return True
    return allow_subdomains and host.endswith("." + configured_host)


def _mentions_site_domain(
    answer_text: str | None,
    mentioned_domains: list[str],
    site_hosts: list[str],
    allow_subdomains: bool,
) -> bool:
    for mentioned in mentioned_domains:
        host = _normalize_host_from_url_or_host(mentioned)
        if host and any(_host_matches(host, site_host, allow_subdomains) for site_host in site_hosts):
            return True
    if not answer_text or not site_hosts:
        return False
    return any(_text_mentions_host(answer_text, host, allow_subdomains) for host in site_hosts)


def _text_mentions_host(text: str, configured_host: str, allow_subdomains: bool) -> bool:
    escaped = re.escape(configured_host)
    if allow_subdomains:
        host_pattern = rf"(?:[a-z0-9-]+\.)*{escaped}"
    else:
        host_pattern = escaped
    return re.search(rf"(?<![a-z0-9.-]){host_pattern}(?![a-z0-9.-])", text, re.IGNORECASE) is not None


def _contains_any(text: str | None, terms: list[str]) -> bool:
    if not text:
        return False
    for term in terms:
        escaped = re.escape(term.strip())
        if not escaped:
            continue
        # Keep aliases as whole terms while allowing whitespace variation.
        escaped = escaped.replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){escaped}(?!\w)", text, re.IGNORECASE):
            return True
    return False


def _clean_terms(terms: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for term in terms:
        value = str(term).strip()
        folded = value.casefold()
        if value and folded not in {item.casefold() for item in cleaned}:
            cleaned.append(value)
    return cleaned


def _citation_list(value: Any) -> list[Any]:
    if value is _MISSING or value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            parsed = [value]
        value = parsed
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _string_list(value: Any) -> list[str]:
    if value is _MISSING or value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                value = parsed
            else:
                value = [value]
        except (TypeError, ValueError):
            value = [value]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    for match in re.finditer(r"https?://[^\s<>\"']+", text, re.IGNORECASE):
        candidate = match.group(0).rstrip(_TRAILING_URL_PUNCTUATION)
        if candidate and candidate not in found:
            found.append(candidate)
    return found


def _question_text(sample: Any) -> str | None:
    direct = _read_first(sample, "question_text", "prompt")
    if direct is not _MISSING and direct is not None:
        return str(direct)
    question = _read(sample, "question")
    if isinstance(question, str):
        return question
    nested = _read(question, "question") if question is not None else None
    return str(nested) if nested is not None else None


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "rate": numerator / denominator if denominator else None,
    }


def _nullable_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _parse_cost(value: Any) -> tuple[Decimal | None, str]:
    if value is _MISSING or value is None or value == "":
        return None, "unknown"
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None, "invalid"
    if not parsed.is_finite() or parsed < 0:
        return None, "invalid"
    return parsed, "known"


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.000001")), "f")


def _timestamp(value: Any) -> str | None:
    if value is _MISSING or value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


_MISSING = object()


def _read(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _read_first(value: Any, *keys: str) -> Any:
    for key in keys:
        result = _read(value, key, _MISSING)
        if result is not _MISSING and result is not None:
            return result
    return _MISSING


