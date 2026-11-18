"""Run paid live cases and isolated deterministic failures, with append-only results."""

import argparse
import asyncio
import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.agent import AgentService
from app.business import DEMO_USER_ID, BusinessStore
from app.config import Settings
from app.providers import OpenAIProvider
from app.retrieval.store import KnowledgeStore
from evaluation.data import DATASETS, ROOT, digest, freeze_datasets, load_dataset, source_hashes
from evaluation.metrics import live_contract, retrieval_scores


async def compare_retrieval(case: dict, store: KnowledgeStore, provider: OpenAIProvider) -> dict | None:
    gold = case["expected"].get("doc_ids", [])
    if not gold:
        return None
    query = "\n".join([*case.get("history", []), case["question"]])
    started = time.perf_counter()
    embedding = await provider.embed_query(query)
    embedding_ms = (time.perf_counter() - started) * 1000
    strategies = ["vector", "exact_vector"]
    if sum(case["id"].encode()) % 2:
        strategies.reverse()
    comparison = {
        "query": query,
        "gold_doc_ids": gold,
        "shared_embedding_tokens": embedding.tokens,
        "shared_embedding_ms": embedding_ms,
        "execution_order": strategies,
        "strategies": {},
    }
    for strategy in strategies:
        started = time.perf_counter()
        result = await store.retrieve(query, top_k=5, strategy=strategy, query_embedding=embedding)
        ids = [chunk.doc_id for chunk in result.chunks]
        comparison["strategies"][strategy] = {
            "doc_ids": ids,
            "chunk_ids": [chunk.chunk_id for chunk in result.chunks],
            "retrieval_only_ms": (time.perf_counter() - started) * 1000,
            **retrieval_scores(ids, gold),
        }
    return comparison


def load_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    results = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            results.append(json.loads(line))
        except ValueError:
            # A partial final line is preserved for inspection, then removed before resuming.
            if index != len(lines) - 1:
                raise ValueError("Corrupt results inside JSONL; do not silently discard records") from None
            path.with_suffix(".partial").write_text(line, encoding="utf-8")
            path.write_text("\n".join(lines[:index]) + "\n", encoding="utf-8")
    ids = [row["case_id"] for row in results]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate result IDs")
    return results


async def execute(args) -> int:
    frozen = freeze_datasets(args.split)
    dataset = load_dataset(args.split)
    cases = dataset["cases"]
    if args.faults_only:
        cases = [case for case in cases if case["execution_mode"] == "fault"]
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
        if len(cases) != len(set(args.case)):
            raise ValueError("Unknown case ID")
    if not cases:
        raise ValueError("No selected cases")
    suffix = "faults" if args.faults_only else "current"
    output = Path(args.output or ROOT / "data/evaluation" / f"{args.split}-{suffix}").resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    provider = OpenAIProvider(settings)
    knowledge = KnowledgeStore(settings, provider)
    hashes = source_hashes()
    metadata = {
        "split": args.split,
        "dataset_sha256": digest(DATASETS / f"{args.split}.json"),
        "source_hashes": hashes,
        "models": {"chat": settings.chat_model, "embedding": settings.embedding_model},
        "parameters": {
            "top_k": 5,
            "max_cosine_distance": settings.max_cosine_distance,
            "chunk_size": settings.chunk_size,
            "chunk_overlap": settings.chunk_overlap,
            "request_timeout_s": settings.request_timeout_s,
            "chat_endpoint_sha256": hashlib.sha256(settings.chat_base_url.encode()).hexdigest(),
            "embedding_endpoint_sha256": hashlib.sha256(settings.embedding_base_url.encode()).hexdigest(),
            "index_manifest_sha256": digest(knowledge.manifest_path)
            if knowledge.manifest_path.exists()
            else None,
        },
        "selected_ids": [case["id"] for case in cases],
        "evaluator_hashes": {path.name: digest(path) for path in sorted(Path(__file__).parent.glob("*.py"))},
    }
    manifest_path = output / "manifest.json"
    if manifest_path.exists():
        if not args.resume:
            raise ValueError("Run directory exists; use --resume or a new --output")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["identity"] != metadata:
            raise ValueError("Dataset, source, model, parameters or selected cases changed; use a new run")
    else:
        if any(output.iterdir()):
            raise ValueError("Output directory must be empty for a new run")
        manifest = {
            "run_id": uuid.uuid4().hex,
            "started_at": datetime.now(UTC).isoformat(),
            "identity": metadata,
            "dataset_freeze": frozen,
            "scope": "Synthetic offline evaluation, local graph calls; no production traffic",
            "runner_sha256": digest(Path(__file__)),
            "status": "running",
        }
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    results_path = output / "results.jsonl"
    existing_rows = load_results(results_path)
    selected = {case["id"]: case for case in cases}
    if any(
        row["case_id"] not in selected or row["case"] != selected[row["case_id"]] for row in existing_rows
    ):
        raise ValueError("Saved results do not match selected frozen cases")
    completed = {row["case_id"] for row in existing_rows}
    pending = [case for case in cases if case["id"] not in completed]
    live_pending = any(case["execution_mode"] == "live" for case in pending)
    if live_pending and (settings.missing_configuration() or knowledge.health()["status"] != "ready"):
        raise ValueError("Local models or index are not ready; run doctor and index first")
    business = BusinessStore(output)
    await asyncio.to_thread(business.initialize)
    handler = RotatingFileHandler(output / "nodes.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logger = logging.getLogger("app.agent")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    gate = asyncio.Semaphore(args.concurrency)
    try:
        async with AsyncSqliteSaver.from_conn_string(str(output / "checkpoints.sqlite3")) as saver:
            await saver.setup()
            service = AgentService(settings, provider, knowledge, business, saver)

            async def one(case):
                async with gate:
                    started = time.perf_counter()
                    row = {
                        "case_id": case["id"],
                        "case": case,
                        "execution_mode": case["execution_mode"],
                        "started_at": datetime.now(UTC).isoformat(),
                    }
                    try:
                        if case["execution_mode"] == "fault":
                            from evaluation.faults import execute_fault

                            row["fault_result"] = await execute_fault(case, settings)
                            row["contract"] = {
                                "passed": row["fault_result"]["passed"],
                                "checks": row["fault_result"]["checks"],
                            }
                        else:
                            session = await asyncio.to_thread(business.create_session, DEMO_USER_ID)
                            session_id = session["session_id"]
                            history_results = []
                            for question in case.get("history", []):
                                history_results.append(await service.ask(session_id, DEMO_USER_ID, question))
                            before = len(await asyncio.to_thread(business.list_tickets, DEMO_USER_ID, 100))
                            response = await service.ask(session_id, DEMO_USER_ID, case["question"])
                            after = len(await asyncio.to_thread(business.list_tickets, DEMO_USER_ID, 100))
                            row.update(
                                session_id=session_id,
                                history_results=history_results,
                                response=response,
                                contract=live_contract(case, response, before, after),
                            )
                            try:
                                row["retrieval_comparison"] = await compare_retrieval(
                                    case, knowledge, provider
                                )
                            except Exception as error:
                                row["retrieval_comparison"] = {"error_type": type(error).__name__}
                    except Exception as error:
                        row.update(
                            runner_error={"type": type(error).__name__, "message": "Case execution failed"},
                            contract={"passed": False, "checks": {"execution": False}},
                        )
                    row["elapsed_seconds"] = time.perf_counter() - started
                    # No await between writing and flushing: concurrent coroutines cannot interleave records.
                    with results_path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                        stream.flush()
                    print(
                        json.dumps(
                            {
                                "case": case["id"],
                                "mode": case["execution_mode"],
                                "contract_pass": row["contract"]["passed"],
                                "status": row.get("response", {}).get("status"),
                                "seconds": round(row["elapsed_seconds"], 2),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

            await asyncio.gather(*(one(case) for case in pending))
        rows = load_results(results_path)
        manifest.update(
            status="completed",
            completed_at=datetime.now(UTC).isoformat(),
            completed_cases=len(rows),
            result_sha256=digest(results_path),
            contract_passes=sum(row["contract"]["passed"] for row in rows),
        )
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {"run": str(output), "completed": len(rows), "contract_passes": manifest["contract_passes"]}
            ),
            flush=True,
        )
        # A completed experiment can contain real model failures; do not relabel those as runner errors.
        return int(any("runner_error" in row for row in rows))
    finally:
        logger.removeHandler(handler)
        handler.close()
        await provider.close()


def main():
    parser = argparse.ArgumentParser(
        description="Frozen evaluation and focused regression (live cases incur API fees)"
    )
    parser.add_argument("--split", choices=("dev", "acceptance", "week4"), default="acceptance")
    parser.add_argument("--output")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--faults-only", action="store_true")
    parser.add_argument("--case", action="append")
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(execute(args)))


if __name__ == "__main__":
    main()
