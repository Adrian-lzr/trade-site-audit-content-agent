from backend.services.visibility_metrics import METRIC_VERSION, calculate_visibility_metrics


def test_mentions_are_separate_from_native_site_citation_and_evaluator_coverage():
    result = calculate_visibility_metrics(
        [
            {
                "question_id": 11,
                "status": "succeeded",
                "answer_text": "Acme valves are available at zoogo.club.",
                "mentioned_domains": ["www.zoogo.club"],
                "citations": [],
                "answered_question": None,
                "provider_kind": "model_api",
            }
        ],
        site_url="https://zoogo.club",
        brand_terms=["Acme"],
        planned_samples=1,
    )

    assert result["metric_version"] == METRIC_VERSION
    assert result["brand_mention"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert result["domain_mention"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert result["validated_site_citation"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert result["answered_question"]["numerator"] == 0
    assert result["answered_question"]["denominator"] == 0
    assert result["answered_question"]["rate"] is None
    assert result["answered_question"]["unknown_question_count"] == 1


def test_only_independent_boolean_evidence_answers_questions_and_question_ids_deduplicate():
    result = calculate_visibility_metrics(
        [
            {"question_id": 4, "status": "succeeded", "answer_text": "A complete text answer", "answered_question": True},
            {"question_id": "4", "status": "failed", "answer_text": None, "answered_question": None},
            {"question_id": 8, "status": "unavailable", "answer_text": None, "answered_question": None},
        ],
        site_url="https://zoogo.club",
        planned_samples=4,
        expected_questions=[{"id": 4}, {"id": 8}, {"id": 9}],
    )

    metric = result["answered_question"]
    assert metric["unique_question_count"] == 3
    assert metric["evaluated_question_count"] == 1
    assert metric["numerator"] == 1
    assert metric["denominator"] == 1
    assert metric["rate"] == 1.0
    assert metric["evaluation_coverage"] == {"numerator": 1, "denominator": 3, "rate": 1 / 3}
    assert result["sample_counts"] == {
        "reported_planned": 4,
        "denominator": 4,
        "observed": 3,
        "succeeded": 1,
        "failed": 1,
        "unavailable": 1,
        "pending": 0,
        "unknown_status": 0,
        "unobserved": 1,
        "unplanned_observed": 0,
    }
    assert result["sample_success"] == {"numerator": 1, "denominator": 4, "rate": 0.25}


def test_conflicting_duplicate_evaluator_results_are_unknown_not_silently_chosen():
    result = calculate_visibility_metrics(
        [
            {"question_id": 1, "status": "succeeded", "answered_question": True},
            {"question_id": 1, "status": "succeeded", "answered_question": False},
        ],
        site_url="https://zoogo.club",
    )

    assert result["answered_question"]["numerator"] == 0
    assert result["answered_question"]["denominator"] == 0
    assert result["answered_question"]["unknown_question_count"] == 1
    assert result["answered_question"]["conflicting_evaluation_count"] == 1


def test_validated_native_citation_uses_host_boundary_and_rejects_bad_or_internal_urls():
    citations = [
        {"url": "https://zoogo.club/products/valve", "kind": "native", "raw_source": "provider-source", "crawled_at": "2026-10-01T12:00:00Z"},
        "https://sub.zoogo.club/catalog",
        "https://zoogo.club.evil.example/page",
        "https://evilzoogo.club/page",
        "https://zoogo.club.attacker.example/",
        "http://127.0.0.1/internal",
        "javascript:alert(1)",
        "https://user:secret@zoogo.club/private",
    ]
    result = calculate_visibility_metrics(
        [{"question_id": 1, "status": "succeeded", "citations": citations}],
        site_url="https://zoogo.club",
        planned_samples=1,
    )

    evidence = result["citation_details"]["citation_evidence"]
    assert result["validated_site_citation"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert [item["validation_status"] for item in evidence] == [
        "site_match",
        "site_match",
        "other_domain",
        "other_domain",
        "other_domain",
        "internal_ip",
        "unsupported_scheme",
        "invalid_url",
    ]
    assert evidence[0]["provider_raw_source"] == "provider-source"
    assert evidence[0]["normalized_domain"] == "zoogo.club"
    assert evidence[0]["crawled_at"] == "2026-10-01T12:00:00Z"
    assert evidence[0]["validated_site_citation"] is True
    assert evidence[2]["validated_site_citation"] is False


def test_subdomain_policy_can_require_exact_site_host():
    result = calculate_visibility_metrics(
        [{"question_id": 1, "status": "succeeded", "citations": ["https://shop.zoogo.club/item"]}],
        site_url="https://zoogo.club",
        allow_subdomains=False,
    )

    evidence = result["citation_details"]["citation_evidence"][0]
    assert evidence["validation_status"] == "other_domain"
    assert result["validated_site_citation"]["rate"] == 0.0


def test_answer_text_urls_are_inferred_and_never_count_as_native_citations():
    result = calculate_visibility_metrics(
        [
            {
                "question_id": 7,
                "status": "succeeded",
                "answer_text": "Read https://zoogo.club/specs for the details.",
                "citations": [],
            }
        ],
        site_url="https://zoogo.club",
    )

    evidence = result["citation_details"]["citation_evidence"]
    assert len(evidence) == 1
    assert evidence[0]["kind"] == "inferred"
    assert evidence[0]["validation_status"] == "site_match"
    assert evidence[0]["validated_site_citation"] is False
    assert result["citation_details"]["inferred_site_link_count"] == 1
    assert result["validated_site_citation"]["numerator"] == 0
    assert result["domain_mention"]["numerator"] == 1


def test_brand_aliases_are_whole_terms_and_domain_mentions_do_not_match_similar_hosts():
    result = calculate_visibility_metrics(
        [
            {
                "question_id": 1,
                "status": "succeeded",
                "answer_text": "Acme   Industrial Valves help; AcmeCo is a different name.",
                "mentioned_domains": ["evilzoogo.club"],
                "citations": [],
            }
        ],
        site_url="https://zoogo.club",
        brand_terms=["Acme", "Acme Industrial Valves"],
    )

    assert result["brand_mention"]["numerator"] == 1
    assert result["domain_mention"]["numerator"] == 0


def test_costs_keep_missing_and_invalid_values_unknown_and_report_estimates_separately():
    result = calculate_visibility_metrics(
        [
            {"question_id": 1, "status": "succeeded", "cost_usd": "0.012300", "estimated_cost_usd": "0.010000"},
            {"question_id": 2, "status": "failed", "cost_usd": None, "estimated_cost_usd": "0.004000"},
            {"question_id": 3, "status": "succeeded", "cost_usd": "-1"},
        ],
        site_url="https://zoogo.club",
        planned_samples=4,
    )

    assert result["cost"] == {
        "currency": "USD",
        "known_actual_cost_usd": "0.012300",
        "known_actual_cost_sample_count": 1,
        "unknown_actual_cost_sample_count": 2,
        "invalid_actual_cost_sample_count": 1,
        "estimated_cost_usd": "0.014000",
        "estimated_cost_sample_count": 2,
        "unobserved_sample_count": 1,
        "complete": False,
        "unknown": True,
        "unknown_cost_is_not_zero": True,
    }


def test_manual_synthetic_and_consumer_surface_sources_remain_separate_strata():
    result = calculate_visibility_metrics(
        [
            {"question_id": 1, "status": "succeeded", "provider_kind": "manual_capture"},
            {"question_id": 2, "status": "succeeded", "provider_kind": "model_api", "is_synthetic": True},
            {"question_id": 3, "status": "failed", "provider_kind": "consumer_search_surface"},
        ],
        site_url="https://zoogo.club",
        planned_samples=3,
    )

    strata = result["evidence_strata"]
    assert strata["mixed"] is True
    assert set(strata["by_category"]) == {"manual", "synthetic", "consumer_search_surface"}
    assert strata["manual_sample_count"] == 1
    assert strata["synthetic_sample_count"] == 1
    assert strata["consumer_search_surface_sample_count"] == 1
    assert strata["by_category"]["manual"]["is_manual"] is True
    assert strata["by_category"]["synthetic"]["is_synthetic"] is True


def test_empty_run_has_explicit_zero_denominators_and_pending_planned_samples():
    result = calculate_visibility_metrics([], site_url="https://zoogo.club", planned_samples=3)

    assert result["sample_success"] == {"numerator": 0, "denominator": 3, "rate": 0.0}
    assert result["sample_counts"]["unobserved"] == 3
    assert result["brand_mention"] == {"numerator": 0, "denominator": 0, "rate": None}
    assert result["validated_site_citation"] == {"numerator": 0, "denominator": 0, "rate": None}
    assert result["cost"]["unknown"] is True
    assert result["cost"]["known_actual_cost_usd"] == "0.000000"

