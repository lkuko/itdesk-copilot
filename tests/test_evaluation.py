"""Metrics and comparison tests use controlled rankings, not a paid model."""

import json

import pytest

from app.retrieval.store import KnowledgeStore
from app.schemas import EmbeddingBatch
from evaluation.data import DATASETS, freeze_datasets, load_dataset
from evaluation.metrics import live_contract, macro_f1, percentile, retrieval_scores
from evaluation.report import summarize
from evaluation.run import load_results


async def test_exact_matching_can_recover_a_document_excluded_by_vector_threshold(settings, provider):
    store = KnowledgeStore(settings, provider)
    await store.build_index()
    wrong_vector = EmbeddingBatch([[0.0, 0.0, 0.0, 1.0]], tokens=5)

    vector = await store.retrieve("VPN-403", strategy="vector", query_embedding=wrong_vector)
    combined = await store.retrieve("VPN-403", strategy="exact_vector", query_embedding=wrong_vector)

    assert not vector.chunks
    assert combined.chunks[0].doc_id == "vpn-registration"
    assert combined.chunks[0].exact_match
    assert provider.queries == []  # One externally measured embedding may be shared by both methods.


def test_metrics_keep_missing_predictions_and_failed_recall_in_denominators():
    report = macro_f1(["vpn", "email"], ["vpn", "missing"])
    assert report["sample_count"] == 2
    assert report["per_class"]["email"]["fn"] == 1
    assert report["macro_f1"] < 1
    assert retrieval_scores([], ["vpn-001"])["recall_at_5"] == 0
    assert retrieval_scores(["wrong", "vpn-001", "vpn-001"], ["vpn-001"])["mrr_at_5"] == 0.5
    assert retrieval_scores([], [])["recall_at_5"] is None
    assert retrieval_scores(["a"] * 5 + ["gold"], ["gold"])["recall_at_5"] == 0
    assert percentile([9, 1, 4], 0.95) == 9


def test_contract_pass_does_not_depend_only_on_fluent_answer():
    case = {
        "expected": {
            "status": ["answered"],
            "required_tool": "search_knowledge",
            "must_not_contain": ["关闭安全软件"],
        }
    }
    response = {
        "status": "answered",
        "answer": "关闭安全软件",
        "citations": [{"chunk_id": "fake"}],
        "retrieved": [],
        "tool_trace": [],
    }
    result = live_contract(case, response, 2, 3)
    assert result["passed"] is False
    assert not result["checks"]["required_tool"]
    assert not result["checks"]["citation_membership"]
    assert not result["checks"]["no_unconfirmed_ticket"]
    assert result["risk_signals"][0]["phrase"] == "关闭安全软件"
    assert result["semantic_review_required"] is True


@pytest.mark.parametrize(
    "answer",
    [
        "不建议先删除本地邮箱数据库重建。请先比较收件时间。",
        "请先删除本地邮箱数据库，再重新登录。",
        "不建议先删除本地邮箱数据库，但仍请先删除本地邮箱数据库。",
    ],
)
def test_literal_risk_phrases_are_review_signals_not_automatic_semantic_judgments(answer):
    case = {"expected": {"status": ["answered"], "must_not_contain": ["先删除本地邮箱数据库"]}}
    result = live_contract(case, {"status": "answered", "answer": answer}, 2, 2)

    # Both the safe negation and unsafe instruction retain visible review evidence.
    # Mechanical success must not silently label either statement semantically safe.
    assert result["passed"] is True
    assert result["semantic_review_required"] is True
    assert len(result["risk_signals"]) == answer.count("先删除本地邮箱数据库")
    assert all(signal["context"] for signal in result["risk_signals"])


def test_absent_literal_risk_signal_is_not_a_claim_that_semantic_review_was_completed():
    case = {"expected": {"status": ["answered"], "must_not_contain": ["先删除本地邮箱数据库"]}}
    result = live_contract(case, {"status": "answered", "answer": "请提供错误码。"}, 0, 0)

    assert result["risk_signals"] == []
    assert result["semantic_review_required"] is False
    assert "not semantic" in result["note"]


def test_resume_preserves_completed_records_and_rejects_interior_corruption(tmp_path):
    path = tmp_path / "results.jsonl"
    row = json.dumps({"case_id": "a"})
    path.write_text(row + '\n{"case_id":', encoding="utf-8")
    assert load_results(path) == [{"case_id": "a"}]
    assert path.with_suffix(".partial").is_file()
    assert load_results(path) == [{"case_id": "a"}]
    path.write_text(row + "\nnot-json\n" + json.dumps({"case_id": "b"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_results(path)


def test_frozen_dataset_and_missing_response_measurements_are_explicit():
    freeze_datasets()  # Must verify existing hashes, never silently re-freeze changed cases.
    assert len(load_dataset("dev")["cases"]) == 20
    acceptance = load_dataset("acceptance")["cases"]
    assert len(acceptance) == 60
    assert sum(case["execution_mode"] == "fault" for case in acceptance) == 10
    row = {
        "execution_mode": "live",
        "case": {
            "kind": "business",
            "expected": {
                "category": "vpn",
                "intent": "troubleshooting",
                "status": ["answered"],
                "doc_ids": [],
            },
        },
        "contract": {"passed": False},
        "case_id": "missing",
        "runner_error": {"type": "Error"},
    }
    metrics = summarize([row])
    assert metrics["latency_seconds"]["p50"] is None
    assert metrics["latency_seconds"]["missing_response_measurements"] == 1
    assert metrics["category"]["sample_count"] == 1


def test_recovered_tool_timeout_is_counted_separately_from_terminal_round_failure():
    row = {
        "case_id": "recovered",
        "execution_mode": "live",
        "case": {
            "kind": "business",
            "expected": {
                "category": "vpn",
                "intent": "troubleshooting",
                "status": ["answered"],
                "doc_ids": [],
            },
        },
        "contract": {"passed": True},
        "response": {
            "status": "answered",
            "tool_trace": [
                {"status": "error", "result": {"error": {"code": "tool_timeout"}}},
                {"status": "rejected", "result": {"error": {"code": "parallel_calls_rejected"}}},
                {"status": "ok", "result": {"ok": True}},
            ],
        },
    }
    metrics = summarize([row])["timeouts"]
    assert metrics["terminal_rounds"] == 0
    assert metrics["tool_timeout_entries"] == 1
    assert metrics["executed_tool_entries"] == 2
    assert metrics["rejected_tool_entries"] == 1
    assert metrics["recovered_rounds_with_tool_timeout"] == 1
    assert metrics["case_ids"] == ["recovered"]


def test_week4_regression_freezes_separately_and_preserves_original_question_replays():
    originals = {
        name: (DATASETS / name).read_bytes() for name in ("dev.json", "acceptance.json", "freeze.json")
    }
    frozen = freeze_datasets("week4")
    cases = load_dataset("week4")["cases"]
    assert set(frozen["datasets"]) == {"week4.json"}
    assert len(cases) == 12
    assert {name: (DATASETS / name).read_bytes() for name in originals} == originals
    old_cases = {
        row["id"]: row for split in ("dev", "acceptance") for row in load_dataset(split)["cases"]
    }
    replays = [case for case in cases if case.get("regression_of")]
    assert len(replays) == 6
    for case in replays:
        original = old_cases[case["regression_of"]]
        assert case["question"] == original["question"]
        assert case["history"] == original["history"]
