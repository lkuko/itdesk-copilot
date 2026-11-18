"""Compare the two knowledge lab chunkers on development questions with document labels."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

from app.business import BusinessError
from app.config import Settings
from app.providers import OpenAIProvider
from app.retrieval.lab import CompareRequest, KnowledgeLab
from app.retrieval.store import KnowledgeStore

DATASET = Path(__file__).with_name("datasets") / "dev.json"


async def experiment(top_k: int) -> dict:
    dataset_bytes = DATASET.read_bytes()
    cases = [
        case for case in json.loads(dataset_bytes)["cases"]
        if case["execution_mode"] == "live" and not case["history"]
        and case["expected"].get("required_tool") == "search_knowledge"
        and case["expected"].get("doc_ids")
    ]
    settings = Settings()
    provider = OpenAIProvider(settings)
    lab = KnowledgeLab(settings, provider, KnowledgeStore(settings, provider))
    try:
        started = perf_counter()
        prepared = await lab.prepare_comparison(timeout=180)
        preparation_ms = (perf_counter() - started) * 1000
        results = []
        for case in cases:
            gold = set(case["expected"]["doc_ids"])
            row = {"id": case["id"], "question": case["question"], "gold_doc_ids": sorted(gold),
                   "reference_facts": case["expected"]["reference_facts"]}
            started = perf_counter()
            try:
                comparison = await lab.compare(CompareRequest(query=case["question"], top_k=top_k),
                                               prepared=prepared)
            except BusinessError as error:
                results.append({**row, "error_code": error.code})
                continue
            strategies = []
            for group in comparison.strategies:
                matches = {hit.doc_id for hit in group.hits} & gold
                first = next((rank for rank, hit in enumerate(group.hits, 1) if hit.doc_id in gold), None)
                strategies.append({**group.model_dump(), "recall": len(matches) / len(gold),
                                   "reciprocal_rank": 1 / first if first else 0.0})
            results.append({**row, "strategies": strategies, "query_embedding_tokens":
                            comparison.query_embedding_tokens, "query_and_rank_ms":
                            round((perf_counter() - started) * 1000, 2)})
    finally:
        await provider.close()

    successful = [row for row in results if "strategies" in row]
    summary = {}
    if successful:
        for group in successful[0]["strategies"]:
            name = group["strategy"]
            scores = [next(s for s in row["strategies"] if s["strategy"] == name) for row in successful]
            summary[name] = {
                "macro_document_recall": mean(score["recall"] for score in scores),
                "mrr_at_k": mean(score["reciprocal_rank"] for score in scores),
                "chunk_count": group["chunk_count"],
                "embedding_characters": group["embedding_characters"],
            }
    corpus_fingerprint = hashlib.sha256(json.dumps(
        {strategy: [(span.chunk.chunk_id, span.chunk.title, span.start, span.end)
                    for span in spans] for strategy, spans in prepared.partitions.items()},
        ensure_ascii=False, sort_keys=True,
    ).encode()).hexdigest()
    return {
        "utc": datetime.now(UTC).isoformat(), "dataset": "dev.json", "dataset_sha256":
        hashlib.sha256(dataset_bytes).hexdigest(), "corpus_partitions_sha256": corpus_fingerprint,
        "mode": "real_embedding_only_development_questions", "top_k": top_k,
        "embedding_model": settings.embedding_model, "embedding_dimension": prepared.dimension,
        "chunk_size": settings.chunk_size, "chunk_overlap": settings.chunk_overlap,
        "unique_chunk_inputs": len(prepared.vectors), "document_embedding_ms": round(preparation_ms, 2),
        "document_embedding_tokens": prepared.document_embedding_tokens,
        "queries": len(results), "successful_queries": len(successful), "summary_on_successes": summary,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--top-k", type=int, choices=range(1, 11), default=5)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("choose a new output path")
    try:
        report = asyncio.run(experiment(args.top_k))
    except BusinessError as error:
        parser.exit(1, f"Embedding preparation failed: {error.code}\n")
    with args.output.open("x", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print(f"Completed {report['successful_queries']}/{report['queries']} queries. See {args.output}.")


if __name__ == "__main__":
    main()
