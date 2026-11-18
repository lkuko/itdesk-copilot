"""Explicit setup commands; no cloud requests without a command asking for them."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from app.config import Settings
from app.errors import ITDeskError
from app.providers import OpenAIProvider
from app.retrieval.ingestion import chunk_documents, load_documents
from app.retrieval.store import KnowledgeStore
from app.schemas import AskResponse
from app.service import AnswerService


def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


async def execute(args) -> int:
    settings = Settings()
    provider = OpenAIProvider(settings)
    store = KnowledgeStore(settings, provider)
    try:
        if args.command == "doctor":
            missing = settings.missing_configuration()
            docs = load_documents(settings.knowledge_dir)
            report = {
                "configuration": "missing" if missing else "present",
                "missing_fields": missing,
                "models": {"chat": settings.chat_model, "embedding": settings.embedding_model},
                "document_count": len(docs),
                "chunk_count": len(chunk_documents(docs)),
                "index": store.health(),
                "live_probe": "not_run",
            }
            if args.live:
                if missing:
                    report["live_probe"] = "blocked_missing_configuration"
                    output(report)
                    return 1
                report["live_probe"] = await provider.probe_tools()
            output(report)
            return int(bool(missing))
        if args.command == "index":
            output((await store.build_index(rebuild=args.rebuild)).model_dump())
            return 0
        if args.command == "ask":
            response = await AnswerService(settings, provider, store).ask(args.question)
            output(response.model_dump())
            return int(response.status == "failed")
        if args.command == "smoke":
            cases_path = Path(__file__).resolve().parents[1] / "tests/data/week1_cases.json"
            cases = json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
            if args.limit:
                cases = cases[: args.limit]
            results = []
            for case in cases:
                response: AskResponse = await AnswerService(settings, provider, store).ask(case["question"])
                result = {**case, "result": response.model_dump()}
                results.append(result)
                print(f"{case['case_id']}: {response.status}", flush=True)
            answerable = [r for r in results if r["answerable"]]
            unanswerable = [r for r in results if not r["answerable"]]
            retrieved_correct = sum(
                bool(set(r["expected_doc_ids"]) & {c["doc_id"] for c in r["result"]["retrieved"]})
                for r in answerable
            )
            report = {
                "note": "开发集冒烟结果；不是冻结验收集或生产指标。答案语义支持率需人工复核。",
                "models": {"chat": settings.chat_model, "embedding": settings.embedding_model},
                "max_cosine_distance": settings.max_cosine_distance,
                "summary": {
                    "cases": len(results),
                    "answerable_cases": len(answerable),
                    "recall_at_5": retrieved_correct / len(answerable) if answerable else None,
                    "unanswerable_cases": len(unanswerable),
                    "refused_unanswerable": sum(r["result"]["status"] == "no_evidence" for r in unanswerable),
                    "failures": sum(r["result"]["status"] == "failed" for r in results),
                },
                "cases": results,
            }
            destination = settings.data_dir / "week1-live-smoke.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            output({"report": str(destination), "summary": report["summary"]})
            return int(report["summary"]["failures"] > 0)
        return 1
    except ITDeskError as error:
        output({"status": "failed", "error": {"code": error.code, "message": error.message}})
        return 1
    except Exception as error:
        output(
            {
                "status": "failed",
                "error": {
                    "code": type(error).__name__,
                    "message": "命令失败；请检查文档、配置和本地目录权限。",
                },
            }
        )
        return 1
    finally:
        await provider.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="ITDesk Copilot Lite 本地工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor = subparsers.add_parser("doctor", help="本地配置检查；--live 才会调用付费接口")
    doctor.add_argument("--live", action="store_true")
    index = subparsers.add_parser("index", help="调用 Embedding API 建立知识索引")
    index.add_argument("--rebuild", action="store_true")
    ask = subparsers.add_parser("ask", help="运行一次真实问答")
    ask.add_argument("question")
    smoke = subparsers.add_parser("smoke", help="运行18条开发集；会调用模型和Embedding API")
    smoke.add_argument("--limit", type=int)
    raise SystemExit(asyncio.run(execute(parser.parse_args())))


if __name__ == "__main__":
    main()
