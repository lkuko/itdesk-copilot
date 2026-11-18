"""Isolated real FastAPI for React integration tests, with no remote model API.

Run with the project's development virtualenv. Every run creates a fresh temporary
business database, checkpoint database and Chroma index. Production data and .env
values are never used by this test application.
"""

import argparse
import asyncio
import re
import sys
import tempfile
from pathlib import Path

import uvicorn
from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.schemas import (  # noqa: E402
    AgentFinal,
    AnswerDraft,
    EmbeddingBatch,
    GeneratedAgentAnswer,
    GeneratedAnswer,
    GeneratedTriage,
    TicketProposal,
    Triage,
)


class DeterministicProvider:
    """Scripted outputs exercise the real Agent/tools/storage, not model quality."""

    @staticmethod
    def _vector(text):
        return [1.0, 0.0, 0.0] if "VPN" in text.upper() else [0.0, 1.0, 0.0]

    async def embed_documents(self, texts):
        return EmbeddingBatch(vectors=[self._vector(text) for text in texts], tokens=0)

    async def embed_query(self, text):
        return EmbeddingBatch(vectors=[self._vector(text)], tokens=0)

    async def generate_answer(self, question, evidence):
        return GeneratedAnswer(AnswerDraft(
            answer="测试资料：VPN-403 请核对设备登记，仍失败时联系 IT。",
            citation_ids=[evidence[0].chunk_id] if evidence else [], evidence_sufficient=bool(evidence),
        ))

    async def agent_triage(self, context):
        question = context["question"]
        intent = (
            "create_ticket" if "准备" in question or "草稿" in question
            else "ticket_status" if "IT-" in question
            else "service_status" if "状态" in question or "正常" in question
            else "other" if "住房" in question
            else "troubleshooting"
        )
        return GeneratedTriage(Triage(category="vpn", intent=intent, priority="normal"))

    async def agent_step(self, messages, tools):
        if messages[-1].type != "human":
            return AIMessage(content="已取得本轮事实。")
        question = str(messages[-1].content)
        if "住房" in question:
            return AIMessage(content="没有适用的只读工具。")
        match = re.search(r"IT-[A-Za-z0-9]+", question)
        if match:
            name, args = "get_ticket_status", {"ticket_id": match.group(0)}
        elif "状态" in question or "正常" in question:
            name, args = "get_service_status", {"service": "vpn"}
        else:
            name, args = "search_knowledge", {"query": question}
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "smoke-tool"}])

    async def agent_final(self, context):
        evidence = context["evidence"]
        trace = context["tool_results"]
        successful = next((item for item in trace if item["status"] == "ok"), None)
        proposal = None
        if "准备" in context["question"] or "草稿" in context["question"]:
            proposal = TicketProposal(
                title="VPN 人工排查", description="用户请求人工核查 VPN。", category="vpn",
            )
        name = successful["name"] if successful else ""
        answer = (
            "VPN 固定模拟服务状态正常，不能代表真实网络。" if name == "get_service_status"
            else "模拟工单查询完成，具体编号与状态见本轮工具结果。" if name == "get_ticket_status"
            else "VPN-403 请核对设备登记，仍失败时联系 IT；这是一条确定性测试回复。"
        )
        return GeneratedAgentAnswer(AgentFinal(
            answer=answer, citation_ids=[evidence[0]["chunk_id"]] if evidence else [],
            evidence_sufficient=bool(successful), ticket_draft=proposal,
        ))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="itdesk-react-integration-") as temporary:
        root = Path(temporary)
        knowledge = root / "knowledge"
        knowledge.mkdir()
        (knowledge / "vpn.md").write_text(
            "---\ndoc_id: vpn-smoke\ntitle: VPN-403 设备登记测试手册\n"
            "category: vpn\nversion: '1.0'\nerror_codes: [VPN-403]\n---\n\n"
            "# VPN-403 设备登记测试手册\n\n虚构测试资料。VPN-403 请核对设备登记，仍失败时联系 IT。\n",
            encoding="utf-8",
        )
        settings = Settings(
            _env_file=None, knowledge_dir=knowledge, data_dir=root / "data",
            chat_base_url="https://unused.example.test/v1", chat_api_key="test-only-unused",
            embedding_base_url="https://unused.example.test/v1", embedding_api_key="test-only-unused",
            chat_model="deterministic-test", embedding_model="deterministic-test",
            cors_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5174"],
        )
        application = create_app(settings=settings, provider=DeterministicProvider())
        asyncio.run(application.state.store.build_index())
        uvicorn.run(application, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
