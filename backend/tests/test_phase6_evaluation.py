from __future__ import annotations

from evals.visibility_eval import STRATEGIES, _percentile, build_cases, run


def test_phase6_fixture_report_records_latency_and_cost_boundaries():
    report = run(build_cases())

    assert report["sample_count"] == 40
    assert report["dev_count"] == 30
    assert report["holdout_count"] == 10
    assert report["measurement"]["cost"].startswith("fixture provider")
    assert len(report["results"]) == 40 * len(STRATEGIES)

    for strategy in STRATEGIES:
        summary = report["strategies"][strategy]
        assert summary["latency_ms"]["p50"] <= summary["latency_ms"]["p95"]
        assert summary["latency_ms"]["p50"] >= 0
        assert summary["cost_usd"]["total"] == 0
        assert summary["cost_usd"]["basis"] == "fixture_zero_cost"

    assert _percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5
