"""Run the deterministic Phase 6 content and fact-constraint evaluation.

The benchmark is intentionally offline. It measures the behavior of the local
fixture strategies against a frozen set of 40 synthetic HTML/fact cases; it is
not a claim about search-engine or consumer AI visibility.
"""

from __future__ import annotations

import argparse
import json
from statistics import fmean
from time import perf_counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DATASET_VERSION = "phase6-frozen-v1"
MODEL_VERSION = "fixture-eval-v1"
PROMPT_VERSION = "procurement-fact-constraint-v1"
STRATEGIES = ("original", "ordinary_rewrite", "fact_constrained_rewrite")


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    split: str
    product: str
    market: str
    language: str
    html: str
    facts: tuple[str, ...]
    buyer_questions: tuple[str, ...]
    forbidden_claims: tuple[str, ...]


@dataclass(frozen=True)
class StrategyResult:
    case_id: str
    split: str
    strategy: str
    factual_consistency: bool
    question_coverage: bool
    forbidden_claims_absent: bool
    score: float
    latency_ms: float
    cost_usd: float


PRODUCTS = (
    ("VX-21", "stainless steel valve", "16 bar", "MOQ 20 pieces"),
    ("VX-22", "flanged butterfly valve", "25 bar", "MOQ 10 pieces"),
    ("VX-23", "pneumatic control valve", "40 bar", "MOQ 5 pieces"),
    ("VX-24", "brass ball valve", "10 bar", "MOQ 50 pieces"),
    ("VX-25", "wafer check valve", "16 bar", "MOQ 30 pieces"),
    ("VX-26", "threaded gate valve", "25 bar", "MOQ 25 pieces"),
    ("VX-27", "sanitary butterfly valve", "10 bar", "MOQ 12 pieces"),
    ("VX-28", "electric actuator valve", "16 bar", "MOQ 8 pieces"),
    ("VX-29", "lined plug valve", "40 bar", "MOQ 6 pieces"),
    ("VX-30", "forged globe valve", "25 bar", "MOQ 15 pieces"),
)
MARKETS = ("US", "CA", "GB", "AU")


def build_cases() -> tuple[EvalCase, ...]:
    """Build the frozen 40-case set from a versioned, deterministic recipe."""

    cases: list[EvalCase] = []
    for index in range(40):
        model, product, pressure, moq = PRODUCTS[index % len(PRODUCTS)]
        market = MARKETS[index // len(PRODUCTS)]
        split = "dev" if index < 30 else "holdout"
        facts = (model, pressure, moq)
        questions = ("quantity", "configuration", "delivery")
        forbidden = ("guaranteed ranking", "fastest delivery")
        html = (
            f"<article><h1>{product}</h1><p>Model {model}; rated pressure {pressure}.</p>"
            f"<p>{moq}; ask for configuration, quantity and delivery point.</p></article>"
        )
        cases.append(
            EvalCase(
                case_id=f"case-{index + 1:02d}",
                split=split,
                product=product,
                market=market,
                language="en",
                html=html,
                facts=facts,
                buyer_questions=questions,
                forbidden_claims=forbidden,
            )
        )
    return tuple(cases)


def candidate(case: EvalCase, strategy: str) -> str:
    if strategy == "original":
        return f"{case.product} for industrial applications. Contact us for details."
    if strategy == "ordinary_rewrite":
        return (
            f"{case.product} for industrial applications with reliable quality. "
            "Ask our team about options and delivery."
        )
    if strategy == "fact_constrained_rewrite":
        model, pressure, moq = case.facts
        return (
            f"{case.product} {model} is rated to {pressure}. The current purchasing note is {moq}. "
            "Tell us the required quantity, configuration and delivery point so the team can confirm the quote."
        )
    raise ValueError(f"unknown strategy: {strategy}")


def evaluate_case(case: EvalCase, strategy: str) -> StrategyResult:
    started = perf_counter()
    text = candidate(case, strategy).lower()
    factual = all(fact.lower() in text for fact in case.facts)
    questions = all(term.lower() in text for term in case.buyer_questions)
    safe = not any(claim.lower() in text for claim in case.forbidden_claims)
    score = round((int(factual) + int(questions) + int(safe)) / 3, 4)
    latency_ms = round((perf_counter() - started) * 1000, 3)
    return StrategyResult(case.case_id, case.split, strategy, factual, questions, safe, score, latency_ms, 0.0)


def _percentile(values: list[float], fraction: float) -> float:
    """Return a linear-interpolated percentile for a non-empty sample."""

    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * weight, 3)


def run(cases: Iterable[EvalCase]) -> dict[str, object]:
    cases = tuple(cases)
    results = [evaluate_case(case, strategy) for case in cases for strategy in STRATEGIES]
    summary: dict[str, dict[str, object]] = {}
    for strategy in STRATEGIES:
        selected = [result for result in results if result.strategy == strategy]
        holdout = [result for result in selected if result.split == "holdout"]
        latencies = [result.latency_ms for result in selected]
        costs = [result.cost_usd for result in selected]
        summary[strategy] = {
            "sample_count": len(selected),
            "holdout_count": len(holdout),
            "factual_consistency_rate": round(sum(result.factual_consistency for result in selected) / len(selected), 4),
            "question_coverage_rate": round(sum(result.question_coverage for result in selected) / len(selected), 4),
            "forbidden_claims_absent_rate": round(sum(result.forbidden_claims_absent for result in selected) / len(selected), 4),
            "mean_score": round(sum(result.score for result in selected) / len(selected), 4),
            "holdout_mean_score": round(sum(result.score for result in holdout) / len(holdout), 4),
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "mean": round(fmean(latencies), 3),
            },
            "cost_usd": {
                "total": round(sum(costs), 6),
                "mean": round(fmean(costs), 6),
                "basis": "fixture_zero_cost",
            },
        }
    return {
        "dataset_version": DATASET_VERSION,
        "model_version": MODEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "sample_count": len(cases),
        "dev_count": sum(case.split == "dev" for case in cases),
        "holdout_count": sum(case.split == "holdout" for case in cases),
        "measurement": {
            "latency": "wall_clock_per_case_strategy",
            "cost": "fixture provider reports zero cost; no live provider pricing was used",
            "warning": "These measurements are local fixture timings and are not a production P50/P95 or billing estimate.",
        },
        "strategies": summary,
        "results": [asdict(result) for result in results],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None, help="write the JSON result to this path")
    args = parser.parse_args()
    report = run(build_cases())
    encoded = json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
