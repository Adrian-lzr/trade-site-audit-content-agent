from backend.app import KNOWLEDGE_ENTRIES
from backend.knowledge import curated_entries, filter_guidance, select_guidance


def test_curated_knowledge_distinguishes_authority_and_internal_heuristics():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    kinds = {entry["id"]: entry["source_kind"] for entry in entries}

    assert len(entries) == 29
    assert kinds["rfq-page-content-checklist"] == "internal_heuristic"
    assert kinds["google-search-essentials"] == "official_guidance"
    assert kinds["ftc-green-guides"] == "official_regulator"
    assert kinds["icc-incoterms-2020"] == "standards_body"
    assert kinds["valve-selection-input-checklist"] == "internal_heuristic"
    assert kinds["product-document-applicability-checklist"] == "internal_heuristic"
    assert kinds["valve-actuator-input-checklist"] == "internal_heuristic"
    assert kinds["valve-test-record-applicability-checklist"] == "internal_heuristic"
    assert kinds["valve-drawing-installation-maintenance-checklist"] == "internal_heuristic"
    assert kinds["eu-pressure-equipment-directive"] == "official_regulator"
    assert all(entry["freshness_policy"] == "revalidate_before_use" for entry in entries)


def test_market_filter_supports_country_aliases_and_scopes_guidance():
    entries = curated_entries(KNOWLEDGE_ENTRIES)

    united_states = filter_guidance(entries, market="US")
    germany = filter_guidance(entries, market="Germany")

    assert {"ftc-green-guides", "ftc-endorsements-reviews", "usitc-hts"}.issubset(
        {entry["id"] for entry in united_states}
    )
    assert "eu-access2markets" not in {entry["id"] for entry in united_states}
    assert "eu-access2markets" in {entry["id"] for entry in germany}


def test_guidance_retrieval_ranks_market_and_topic_and_obeys_limit():
    entries = curated_entries(KNOWLEDGE_ENTRIES)

    selected = select_guidance(
        entries,
        {
            "question": "Which tariff and commodity code applies to this offer?",
            "target_market": "United Kingdom",
            "language": "en",
        },
        limit=8,
    )
    ids = [entry["id"] for entry in selected]

    assert len(selected) <= 8
    assert len(selected) >= 3
    assert "uk-trade-tariff" in ids[:3]
    assert "source-freshness-fact-separation" in ids
    assert "rfq-page-content-checklist" not in ids
    assert all(entry["entry_type"] == "external_guidance" for entry in selected)


def test_request_summary_prioritizes_page_field_guidance_over_market_only_sources():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    selected = select_guidance(
        entries,
        {
            "question": "How should buyers compare this industrial valve?",
            "request_summary": "Prepare the product page SEO title and meta description.",
            "target_market": "Germany",
            "language": "en",
        },
        limit=8,
    )
    ids = [entry["id"] for entry in selected]

    assert "google-title-links" in ids
    assert "google-meta-descriptions" in ids
    assert "eu-access2markets" not in ids
    assert "eu-pressure-equipment-directive" not in ids


def test_valve_selection_guidance_matches_english_and_chinese_queries_without_compliance_noise():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    contexts = (
        {
            "question": "How should we select an industrial valve for pressure, temperature, media compatibility, and materials?",
            "target_market": "Germany",
        },
        {
            "question": "阀门选型要先收集压力、温度、介质兼容性和材料哪些资料？",
            "target_market": "Germany",
        },
    )

    for context in contexts:
        ids = {entry["id"] for entry in select_guidance(entries, context, limit=8)}
        assert "valve-selection-input-checklist" in ids
        assert "eu-pressure-equipment-directive" not in ids
        assert "usitc-hts" not in ids


def test_pressure_equipment_and_certificate_query_selects_market_boundary_and_document_checklist():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    selected = select_guidance(
        entries,
        {
            "question": "Which pressure equipment directive and conformity assessment apply to this valve model certificate?",
            "product": "Industrial valve",
            "target_market": "Germany",
        },
        limit=8,
    )
    ids = {entry["id"] for entry in selected}

    assert "eu-pressure-equipment-directive" in ids
    assert "product-document-applicability-checklist" in ids
    assert "usitc-hts" not in ids
    assert "uk-trade-tariff" not in ids


def test_plural_certificate_query_matches_document_checklist_without_assuming_a_market():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    selected = select_guidance(
        entries,
        {
            "question": "Which certificates should a buyer verify for a valve shipment to the target market?",
            "product": "Industrial valves",
            "target_market": "To be confirmed",
        },
        limit=8,
    )
    ids = {entry["id"] for entry in selected}

    assert "product-document-applicability-checklist" in ids
    assert "eu-pressure-equipment-directive" not in ids
    assert "usitc-hts" not in ids


def test_general_maintenance_review_does_not_retrieve_search_audit_guidance():
    entries = curated_entries(KNOWLEDGE_ENTRIES)
    selected = select_guidance(
        entries,
        {
            "question": "What maintenance details should a buyer review before choosing an industrial valve?",
            "product": "Industrial valves",
            "target_market": "To be confirmed",
        },
        limit=8,
    )
    ids = {entry["id"] for entry in selected}

    assert "valve-drawing-installation-maintenance-checklist" in ids
    assert "technical-signal-interpretation" not in ids
