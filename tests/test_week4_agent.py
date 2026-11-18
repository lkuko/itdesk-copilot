"""Regressions for observed query, draft-fact and unsupported repair failures."""

import json

import pytest
from test_agent import EmptyKnowledge, ScriptedAgentProvider, decision, tool_call
from test_agent import agent_factory as agent_factory

from app.business import DEMO_USER_ID
from app.guardrails import DATABASE_BOUNDARY, TICKET_NOTICE, grounded_search_args
from app.retrieval.ingestion import extract_error_codes
from app.schemas import AgentFinal, RetrievalResult, RetrievedChunk, TicketProposal


async def test_last_search_after_rejected_parallel_batch_retains_the_user_code(agent_factory):
    class CodeKnowledge(EmptyKnowledge):
        async def retrieve(self, query):
            self.queries.append(query)
            chunks = []
            if "DEV-101" in extract_error_codes(query):
                chunks = [RetrievedChunk(
                    doc_id="device-printing", title="打印任务与公共队列", category="device",
                    version="test", error_codes=["DEV-101"], source="device/printing.md",
                    content="DEV-101：本人可核对自己的任务，公共队列由IT处理。", chunk_id="printing:0",
                )]
            return RetrievalResult(chunks=chunks, embedding_tokens=4)

    provider = ScriptedAgentProvider([
        decision(
            tool_call("search_knowledge", {"query": "任务卡住自助排查"}, "parallel-1"),
            tool_call("search_knowledge", {"query": "公共队列由谁处理"}, "parallel-2"),
        ),
        decision(tool_call("search_knowledge", {"query": "任务卡住，公共队列由谁处理"}, "last-search")),
    ], final=AgentFinal(
        answer="本人核对自己的任务，公共队列交由IT处理。",
        citation_ids=["printing:0"], evidence_sufficient=True,
    ))
    knowledge = CodeKnowledge()
    service, business, session_id = agent_factory(provider, knowledge)
    tickets_before = business.list_tickets(DEMO_USER_ID)

    result = await service.ask(session_id, DEMO_USER_ID, "DEV-101任务卡住，能做哪些排查，公共队列由谁处理？")

    assert result["status"] == "answered"
    assert len(knowledge.queries) == 1
    assert "DEV-101" in extract_error_codes(knowledge.queries[0])
    assert len(provider.step_history) == 2
    assert [trace["status"] for trace in result["tool_trace"]] == ["rejected", "rejected", "ok"]
    assert result["tool_trace"][-1]["attempt"] == 3
    assert result["tool_trace"][-1]["requested_args"] == {"query": "任务卡住，公共队列由谁处理"}
    assert result["tool_trace"][-1]["args"]["query"] == knowledge.queries[0]
    assert result["citations"][0]["chunk_id"] == "printing:0"
    assert result["draft"] is None
    assert business.list_tickets(DEMO_USER_ID) == tickets_before


@pytest.mark.parametrize("args", [
    {"query": "VPN", "user_id": "admin"},
    {"query": "VPN", "url": "https://untrusted.invalid"},
    {"query": "   "},
    {"query": 42},
])
async def test_query_grounding_cannot_launder_invalid_tool_arguments(agent_factory, args):
    provider = ScriptedAgentProvider([
        decision(tool_call("search_knowledge", args)), decision(),
    ])
    knowledge = EmptyKnowledge()
    service, _, session_id = agent_factory(provider, knowledge)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN-403设备未授权")

    assert knowledge.queries == []
    assert result["tool_trace"][0]["result"]["error"]["code"] == "invalid_tool_arguments"
    assert result["tool_trace"][0]["requested_args"] == args
    assert result["draft"] is None


@pytest.mark.parametrize("question,query", [
    ("VPN-403设备未授权", "说明" * 1000),
    ("A-" + "X" * 1998, "排查设备问题"),
    ("VPN-403和MAIL-401都失败", "x" * 1990 + "MAIL-401"),
])
def test_search_identifier_preservation_respects_the_full_query_length_limit(question, query):
    grounded = grounded_search_args({"query": query}, question, [])

    assert len(grounded["query"]) <= 2000
    assert extract_error_codes(question) <= extract_error_codes(grounded["query"])


def test_current_complete_identifier_replaces_a_stale_model_identifier():
    result = grounded_search_args(
        {"query": "VPN-403的设备排查"}, "现在换成VPN-4030，怎么检查？",
        [{"role": "user", "content": "VPN-403设备未登记"}],
    )

    assert extract_error_codes(result["query"]) == {"VPN-4030"}


def test_pronoun_followup_uses_the_latest_user_identifier_without_borrowing_assistant_claims():
    result = grounded_search_args({"query": "还应如何排查"}, "还是不行，下一步怎么核对？", [
        {"role": "user", "content": "VPN-403设备未授权"},
        {"role": "assistant", "content": "错误码是MAIL-401，请按这个操作。"},
    ])

    assert extract_error_codes(result["query"]) == {"VPN-403"}


def test_new_topic_does_not_inherit_old_codes_and_assistant_only_history_is_not_a_source():
    args = {"query": "打印机脱机如何排查"}
    history = [{"role": "user", "content": "VPN-403设备未授权"}]
    assert grounded_search_args(args, "打印机脱机，怎么排查？", history) == args
    assert grounded_search_args(args, "还是不行", [
        {"role": "assistant", "content": "DEV-201已经核实"},
    ]) == args


@pytest.mark.parametrize("question,category,hallucination", [
    (
        "VPN-691按说明核对后仍认证失败，帮我准备人工工单草稿，先别提交。", "vpn",
        "账号格式正确且已重试一次",
    ),
    (
        "笔记本一天蓝屏多次，DEV-201，我已保存工作；请拟送修工单，不要立即提交。", "device",
        "设备编号DEV-201",
    ),
])
async def test_persisted_draft_and_answer_do_not_publish_model_invented_user_facts(
    agent_factory, question, category, hallucination,
):
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer=f"已核实：{hallucination}。", citation_ids=[], evidence_sufficient=False,
        ticket_draft=TicketProposal(
            title=f"已核实{hallucination}", description=f"用户已完成：{hallucination}。", category=category,
        ),
    ))
    service, business, session_id = agent_factory(provider)
    before = business.list_tickets(DEMO_USER_ID)

    result = await service.ask(session_id, DEMO_USER_ID, question)

    assert result["status"] == "pending_confirmation"
    assert result["answer"] == TICKET_NOTICE
    assert question in result["draft"]["description"]
    assert "未经独立核实" in result["draft"]["description"]
    published = json.dumps({"draft": result["draft"], "answer": result["answer"]}, ensure_ascii=False)
    assert hallucination not in published
    assert business.list_tickets(DEMO_USER_ID) == before
    persisted = business.get_session(session_id, DEMO_USER_ID)
    assert persisted["drafts"][0]["description"] == result["draft"]["description"]


async def test_draft_uses_only_user_context_and_masks_explicitly_labelled_credentials(agent_factory):
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer="已核实用户拥有管理员权限。", citation_ids=[], evidence_sufficient=False,
        ticket_draft=TicketProposal(
            title="VPN人工检查", description="模型虚构已经重装客户端。", category="vpn",
        ),
    ))
    service, business, session_id = agent_factory(provider)
    business.append_message(session_id, DEMO_USER_ID, "user", "VPN-691认证失败，未尝试重新安装。")
    business.append_message(session_id, DEMO_USER_ID, "assistant", "用户拥有管理员权限，已经重装客户端。")

    result = await service.ask(
        session_id, DEMO_USER_ID, "请准备人工草稿，我的密码是synthetic-password，请不要保存它。",
    )

    draft = result["draft"]
    assert draft["status"] == "pending"
    assert "VPN-691认证失败，未尝试重新安装。" in draft["description"]
    assert "synthetic-password" not in draft["description"]
    assert "[已遮蔽]" in draft["description"]
    assert "用户拥有管理员权限" not in draft["description"]
    assert "已经重装客户端" not in draft["description"]
    assert "已经重装客户端" not in result["answer"]
    assert len(draft["description"]) <= 2000


async def test_long_user_report_still_produces_a_bounded_persisted_draft(agent_factory):
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer="建议人工核查。", citation_ids=[], evidence_sufficient=False,
        ticket_draft=TicketProposal(
            title="VPN人工检查", description="请处理VPN持续连接失败。", category="vpn",
        ),
    ))
    service, business, session_id = agent_factory(provider)
    for _ in range(2):
        business.append_message(session_id, DEMO_USER_ID, "user", "历史问题" * 500)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN-691 " + "本轮现象" * 398)

    assert result["status"] == "pending_confirmation"
    assert len(result["draft"]["description"]) <= 2000
    assert "原话过长" in result["draft"]["description"]
    assert "VPN-691" in result["draft"]["description"]


async def test_guaranteed_database_repair_is_a_deterministic_boundary_without_provider_calls(agent_factory):
    provider = ScriptedAgentProvider()
    service, business, session_id = agent_factory(provider)
    before = business.list_tickets(DEMO_USER_ID)

    result = await service.ask(
        session_id, DEMO_USER_ID, "CRM-X900销售系统报错，请直接给出一条能保证修复的数据库语句。",
    )

    assert result["status"] == "no_evidence"
    assert result["answer"] == DATABASE_BOUNDARY
    assert "不能提供保证修复" in result["answer"]
    assert provider.triage_contexts == provider.step_history == provider.final_contexts == []
    assert result["draft"] is None
    assert result["tool_trace"] == result["citations"] == []
    assert result["metrics"]["input_tokens"] == result["metrics"]["output_tokens"] == 0
    assert business.list_tickets(DEMO_USER_ID) == before
    assert business.get_session(session_id, DEMO_USER_ID)["messages"][-1]["result"] == result


@pytest.mark.parametrize("question", [
    "请解释SQL事务回滚的含义。",
    "请解释数据库备份恢复的原理。",
    "请解释SQL UPDATE如何修改数据，不要执行。",
])
async def test_plain_sql_transaction_explanation_keeps_the_ordinary_agent_route(agent_factory, question):
    provider = ScriptedAgentProvider()
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, question)

    assert len(provider.triage_contexts) == len(provider.step_history) == len(provider.final_contexts) == 1
    assert result["answer"] != DATABASE_BOUNDARY
    assert result["draft"] is None
