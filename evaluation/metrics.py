"""Transparent metrics; semantic support is supplied by an explicit review artifact."""

import math
from collections import Counter


def macro_f1(gold: list[str], predicted: list[str]) -> dict:
    if len(gold) != len(predicted):
        raise ValueError("Gold and prediction lengths differ")
    labels = sorted(set(gold) | set(predicted))
    per_class = {}
    for label in labels:
        tp = sum(g == p == label for g, p in zip(gold, predicted, strict=True))
        fp = sum(g != label and p == label for g, p in zip(gold, predicted, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, predicted, strict=True))
        per_class[label] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0,
        }
    return {
        "macro_f1": sum(v["f1"] for v in per_class.values()) / len(labels) if labels else None,
        "sample_count": len(gold),
        "per_class": per_class,
        "confusion": dict(Counter(f"{g} → {p}" for g, p in zip(gold, predicted, strict=True))),
    }


def percentile(values: list[float], quantile: float) -> float | None:
    if not 0 < quantile <= 1:
        raise ValueError("Quantile must be in (0,1]")
    return sorted(values)[math.ceil(len(values) * quantile) - 1] if values else None


def retrieval_scores(doc_ids: list[str], expected: list[str]) -> dict:
    doc_ids = doc_ids[:5]
    relevant = set(expected)
    seen = list(dict.fromkeys(doc_ids))
    matching = relevant & set(seen)
    first = next((index + 1 for index, doc_id in enumerate(doc_ids) if doc_id in relevant), None)
    return {
        "recall_at_5": len(matching) / len(relevant) if relevant else None,
        "hit_at_5": bool(matching) if relevant else None,
        "mrr_at_5": 1 / first if first else 0 if relevant else None,
    }


def live_contract(case: dict, response: dict, ticket_count_before: int, ticket_count_after: int) -> dict:
    expected = case["expected"]
    traces = response.get("tool_trace", [])
    citations = response.get("citations", [])
    retrieved = {chunk["chunk_id"] for chunk in response.get("retrieved", [])}
    required_tool = expected.get("required_tool")
    answer = response.get("answer", "")
    risk_signals = []
    for phrase in expected.get("must_not_contain", []):
        start = 0
        while phrase and (position := answer.find(phrase, start)) >= 0:
            risk_signals.append(
                {
                    "phrase": phrase,
                    "start": position,
                    "context": answer[max(0, position - 30) : position + len(phrase) + 30],
                }
            )
            start = position + len(phrase)
    checks = {
        "status": response.get("status") in expected["status"],
        "required_tool": not required_tool or any(trace["name"] == required_tool for trace in traces),
        "citation_membership": all(citation["chunk_id"] in retrieved for citation in citations),
        "no_unconfirmed_ticket": ticket_count_after == ticket_count_before,
        "tool_attempts": max((trace.get("attempt", 0) for trace in traces), default=0) <= 3,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "risk_signals": risk_signals,
        "semantic_review_required": bool(risk_signals),
        "note": "Mechanical contract v2: literal risk phrases are review signals, not automatic "
        "unsafe-claim judgments; negation and quotation require source review. A mechanical pass "
        "is not semantic answer grading, safety approval or task-resolution accuracy.",
    }
