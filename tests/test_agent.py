"""Bounded read-only execution and durable Agent conversation regressions."""

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError

from app.agent import AgentService
from app.business import DEMO_USER_ID, BusinessError, BusinessStore
from app.errors import ITDeskError
from app.schemas import (
    AgentFinal,
    GeneratedAgentAnswer,
    GeneratedTriage,
    RetrievalResult,
    TicketProposal,
    Triage,
)
from app.tools import ToolExecutor


class EmptyKnowledge:
    def __init__(self):
        self.queries = []
        self.error = None

    async def retrieve(self, query):
        self.queries.append(query)
        if self.error:
            raise self.error
        return RetrievalResult(chunks=[], embedding_tokens=4)


@pytest.fixture
def executor(tmp_path):
    business = BusinessStore(tmp_path / "business")
    business.initialize()
    return ToolExecutor(business, EmptyKnowledge())


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("delete_ticket", {"ticket_id": "IT-1001"}),
        ("get_ticket_status", {"ticket_id": "IT-2001", "user_id": "other-demo-user"}),
        ("get_ticket_status", {"ticket_id": 1001}),
        ("get_ticket_status", {"ticket_id": "IT-1001' OR 1=1--"}),
        ("get_service_status", {"service": "database"}),
        ("get_service_status", {"service": True}),
        ("search_knowledge", {"query": "   "}),
        ("search_knowledge", {"query": "VPN", "url": "https://attacker.invalid"}),
    ],
)
async def test_unapproved_tools_and_arguments_execute_nothing(executor, name, args):
    before = executor.store.list_tickets(DEMO_USER_ID)

    result = await executor.execute(name, args, DEMO_USER_ID)

    assert result["ok"] is False
    assert result["error"]["code"] in {"unknown_tool", "invalid_tool_arguments"}
    assert executor.knowledge_store.queries == []
    assert executor.store.list_tickets(DEMO_USER_ID) == before


async def test_ticket_tool_hides_foreign_records_as_unknown(executor):
    own = await executor.execute("get_ticket_status", {"ticket_id": "IT-1001"}, DEMO_USER_ID)
    foreign = await executor.execute("get_ticket_status", {"ticket_id": "IT-2001"}, DEMO_USER_ID)
    missing = await executor.execute("get_ticket_status", {"ticket_id": "IT-9999"}, DEMO_USER_ID)

    assert own["ok"] is True
    assert own["data"]["ticket_id"] == "IT-1001"
    assert own["data"]["is_simulated"] is True
    assert foreign == missing
    assert foreign["ok"] is False
    assert "其他用户的邮箱工单" not in json.dumps(foreign, ensure_ascii=False)


async def test_service_status_discloses_fixture_age_and_simulation(executor):
    result = await executor.execute("get_service_status", {"service": "email"}, DEMO_USER_ID)

    assert result["ok"] is True
    assert result["data"]["status"] == "degraded"
    assert result["data"]["is_simulated"] is True
    assert result["data"]["observed_at"]
    assert "固定演示快照" in result["data"]["notice"]


async def test_tool_exception_returns_structured_sanitized_error(executor):
    executor.knowledge_store.error = RuntimeError("secret-key-must-not-leak")

    result = await executor.execute("search_knowledge", {"query": "VPN"}, DEMO_USER_ID)

    assert result["ok"] is False
    assert result["error"]["code"] == "tool_failed"
    assert "secret-key-must-not-leak" not in json.dumps(result)


def tool_call(name, args, identifier="call-1"):
    return {"name": name, "args": args, "id": identifier, "type": "tool_call"}


def decision(*calls):
    return AIMessage(
        content="ignored planner prose",
        tool_calls=list(calls),
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    )


class ScriptedAgentProvider:
    def __init__(self, steps=None, final=None):
        self.steps = list(steps or [])
        self.step_history = []
        self.final_contexts = []
        self.final_override = final
        self.triage_contexts = []

    async def agent_triage(self, context):
        self.triage_contexts.append(context.copy())
        return GeneratedTriage(Triage(
            category="vpn", intent="troubleshooting", priority="normal", missing_fields=[], needs_info=False
        ), 0, 0)

    async def agent_step(self, messages, tools):
        self.step_history.append(list(messages))
        return self.steps.pop(0) if self.steps else decision()

    async def agent_final(self, context):
        self.final_contexts.append(context.copy())
        observations = any(item["status"] == "ok" for item in context["tool_results"])
        final = self.final_override or AgentFinal(
            answer="根据本轮模拟工具结果，请联系 IT 核查。", citation_ids=[], evidence_sufficient=observations
        )
        return GeneratedAgentAnswer(final, input_tokens=7, output_tokens=3)


@pytest.fixture
async def agent_factory(settings, tmp_path):
    business = BusinessStore(tmp_path / "agent-business")
    business.initialize()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoint.sqlite3")) as saver:
        await saver.setup()

        def factory(provider, knowledge=None):
            service = AgentService(settings, provider, knowledge or EmptyKnowledge(), business, saver)
            session = business.create_session(DEMO_USER_ID)
            return service, business, session["session_id"]

        yield factory


async def test_invalid_calls_use_three_attempt_budget_and_include_planner_usage(agent_factory):
    provider = ScriptedAgentProvider([decision(tool_call("write_ticket", {}))] * 4)
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "请直接提交工单")

    assert len(provider.step_history) == 3
    assert len(result["tool_trace"]) == 3
    assert [trace["attempt"] for trace in result["tool_trace"]] == [1, 2, 3]
    assert all(trace["status"] == "error" for trace in result["tool_trace"])
    assert result["draft"] is None
    assert result["status"] == "no_evidence"
    assert result["metrics"]["input_tokens"] == 37
    assert result["metrics"]["output_tokens"] == 9


async def test_structured_triage_missing_details_skips_all_tools_and_final_generation(agent_factory):
    class NeedsDetailsProvider(ScriptedAgentProvider):
        async def agent_triage(self, context):
            self.triage_contexts.append(context.copy())
            return GeneratedTriage(Triage(
                category="vpn", intent="troubleshooting", priority="normal",
                missing_fields=["具体错误码或故障现象"], needs_info=True,
            ), 13, 5)

    provider = NeedsDetailsProvider()
    service, business, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "不好用了")

    assert result["status"] == "needs_info"
    assert result["classification"]["needs_info"] is True
    assert result["classification"]["missing_fields"] == ["具体错误码或故障现象"]
    assert "具体错误码或故障现象" in result["answer"]
    assert provider.step_history == provider.final_contexts == []
    assert result["tool_trace"] == result["retrieved"] == result["citations"] == []
    assert result["draft"] is None
    assert result["metrics"]["input_tokens"] == 13
    assert result["metrics"]["output_tokens"] == 5
    assert business.get_session(session_id, DEMO_USER_ID)["messages"][-1]["result"] == result
    snapshot = await service.graph.aget_state({"configurable": {"thread_id": session_id}})
    assert snapshot.next == ()
    assert snapshot.values["saved"] == result


async def test_malformed_triage_repairs_once_then_fails_without_planning(agent_factory):
    class MalformedTriageProvider(ScriptedAgentProvider):
        async def agent_triage(self, context):
            self.triage_contexts.append(context.copy())
            error = ITDeskError("invalid_triage", "模型分诊结构无效")
            error.input_tokens, error.output_tokens = 6, 2
            raise error

    provider = MalformedTriageProvider()
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN 出现故障")

    assert len(provider.triage_contexts) == 2
    assert provider.step_history == provider.final_contexts == []
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_triage"
    assert result["metrics"]["input_tokens"] == 12
    assert result["metrics"]["output_tokens"] == 4
    assert result["draft"] is None


async def test_triage_can_recover_after_one_invalid_output(agent_factory):
    class RepairingTriageProvider(ScriptedAgentProvider):
        async def agent_triage(self, context):
            if not self.triage_contexts:
                self.triage_contexts.append(context.copy())
                error = ITDeskError("invalid_triage", "模型分诊结构无效")
                error.input_tokens, error.output_tokens = 6, 2
                raise error
            return await super().agent_triage(context)

    provider = RepairingTriageProvider()
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN 出现故障")

    assert len(provider.triage_contexts) == 2
    assert len(provider.step_history) == len(provider.final_contexts) == 1
    assert result["status"] == "no_evidence"
    assert result["classification"]["category"] == "vpn"
    assert result["classification"]["intent"] == "troubleshooting"
    assert result["classification"]["priority"] == "normal"
    assert result["metrics"]["input_tokens"] == 23
    assert result["metrics"]["output_tokens"] == 7


async def test_parallel_batch_is_rejected_without_running_any_of_its_tools(agent_factory, monkeypatch):
    calls = [tool_call("get_service_status", {"service": name}, f"call-{name}")
             for name in ("vpn", "email", "printing")]
    provider = ScriptedAgentProvider([decision(*calls)])
    service, _, session_id = agent_factory(provider)
    executions = []

    async def execute(*args):
        executions.append(args)
        return {"ok": True, "data": {}}

    monkeypatch.setattr(service.tools, "execute", execute)
    result = await service.ask(session_id, DEMO_USER_ID, "请查询全部服务")

    assert executions == []
    assert len(provider.step_history) == 1
    assert len(result["tool_trace"]) == 3
    assert all(trace["status"] == "rejected" for trace in result["tool_trace"])
    assert result["status"] == "no_evidence"


async def test_tool_error_is_passed_back_to_model_before_serial_recovery(agent_factory):
    knowledge = EmptyKnowledge()
    knowledge.error = RuntimeError("do-not-display-provider-secret")
    provider = ScriptedAgentProvider([
        decision(tool_call("search_knowledge", {"query": "VPN"})),
        decision(tool_call("get_service_status", {"service": "vpn"}, "call-2")),
        decision(),
    ])
    service, _, session_id = agent_factory(provider, knowledge)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN 怎么了？")

    returned_error = provider.step_history[1][-1]
    assert isinstance(returned_error, ToolMessage)
    assert json.loads(returned_error.content)["error"]["code"] == "tool_failed"
    assert [trace["status"] for trace in result["tool_trace"]] == ["error", "ok"]
    assert result["status"] == "answered"
    assert "do-not-display-provider-secret" not in json.dumps(result)


async def test_invented_citations_are_repaired_once_then_blocked(agent_factory):
    provider = ScriptedAgentProvider(
        [decision(tool_call("get_service_status", {"service": "vpn"})), decision()],
        final=AgentFinal(answer="未经证实的敏感答案", citation_ids=["invented"], evidence_sufficient=True),
    )
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "VPN 当前状态？")

    assert len(provider.final_contexts) == 2
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_citations"
    assert result["citations"] == []
    assert "未经证实的敏感答案" not in result["answer"]


async def test_malformed_final_output_has_one_repair_and_retains_known_billed_tokens(agent_factory):
    class MalformedProvider(ScriptedAgentProvider):
        async def agent_final(self, context):
            self.final_contexts.append(context.copy())
            error = ITDeskError("invalid_model_output", "模型结果格式不合法")
            error.input_tokens, error.output_tokens = 9, 4
            raise error

    provider = MalformedProvider()
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "模型结果异常时怎么办？")

    assert len(provider.final_contexts) == 2
    assert result["status"] == "failed"
    assert result["error"]["code"] == "invalid_model_output"
    assert result["metrics"]["input_tokens"] == 28
    assert result["metrics"]["output_tokens"] == 10
    assert result["metrics"]["cost_usd"] is None


async def test_model_assertion_of_evidence_cannot_replace_actual_current_turn_evidence(agent_factory):
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer="声称公司发放住房补贴", citation_ids=[], evidence_sufficient=True
    ))
    service, _, session_id = agent_factory(provider)

    result = await service.ask(session_id, DEMO_USER_ID, "公司是否发放住房补贴？")

    assert result["status"] == "no_evidence"
    assert result["retrieved"] == result["citations"] == result["tool_trace"] == []
    assert "声称公司发放住房补贴" not in result["answer"]


async def test_draft_proposal_never_creates_ticket_and_request_retry_replays(agent_factory):
    proposal = TicketProposal(
        title="VPN 设备登记异常", description="VPN-403 持续失败，需人工检查。", category="vpn"
    )
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer="将交由 IT 人工检查", citation_ids=[], evidence_sufficient=False, ticket_draft=proposal
    ))
    service, business, session_id = agent_factory(provider)
    before = len(business.list_tickets(DEMO_USER_ID))

    first = await service.ask(session_id, DEMO_USER_ID, "请帮我准备工单", "request-1234")
    replay = await service.ask(session_id, DEMO_USER_ID, "请帮我准备工单", "request-1234")

    assert replay == first
    assert first["status"] == "pending_confirmation"
    assert first["draft"]["status"] == "pending"
    assert len(provider.final_contexts) == 1
    assert len(business.get_session(session_id, DEMO_USER_ID)["drafts"]) == 1
    assert len(business.list_tickets(DEMO_USER_ID)) == before


async def test_same_request_id_cannot_be_reused_for_changed_question(agent_factory):
    provider = ScriptedAgentProvider()
    service, _, session_id = agent_factory(provider)
    await service.ask(session_id, DEMO_USER_ID, "原问题", "request-1234")

    with pytest.raises(BusinessError) as caught:
        await service.ask(session_id, DEMO_USER_ID, "篡改后的问题", "request-1234")

    assert caught.value.code == "request_id_conflict"
    assert len(provider.final_contexts) == 1


async def test_checkpoint_failure_after_business_commit_returns_one_durable_draft(agent_factory, monkeypatch):
    provider = ScriptedAgentProvider(final=AgentFinal(
        answer="需要人工核查", citation_ids=[], evidence_sufficient=False,
        ticket_draft=TicketProposal(
            title="VPN 设备登记故障", description="VPN-403 持续失败。", category="vpn"
        ),
    ))
    service, business, session_id = agent_factory(provider)
    saver = service.graph.checkpointer
    original_put = saver.aput
    failed = False

    async def fail_after_commit(config, checkpoint, metadata, new_versions):
        nonlocal failed
        if checkpoint.get("channel_values", {}).get("saved") and not failed:
            failed = True
            raise RuntimeError("simulated checkpoint failure after business commit")
        return await original_put(config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(saver, "aput", fail_after_commit)
    result = await service.ask(session_id, DEMO_USER_ID, "请准备 VPN 工单", "request-commit-1234")
    replay = await service.ask(session_id, DEMO_USER_ID, "请准备 VPN 工单", "request-commit-1234")

    assert failed is True
    assert result["status"] == "pending_confirmation"
    assert result == replay
    saved_session = business.get_session(session_id, DEMO_USER_ID)
    assert len(saved_session["drafts"]) == 1
    assert len(saved_session["messages"]) == 2
    assert len(provider.final_contexts) == 1


async def test_concurrent_same_session_request_is_rejected_without_second_model_call(agent_factory):
    started, release = asyncio.Event(), asyncio.Event()

    class SlowProvider(ScriptedAgentProvider):
        async def agent_step(self, messages, tools):
            started.set()
            await release.wait()
            return await super().agent_step(messages, tools)

    provider = SlowProvider()
    service, business, session_id = agent_factory(provider)
    first = asyncio.create_task(service.ask(session_id, DEMO_USER_ID, "第一条问题", "request-first"))
    await asyncio.wait_for(started.wait(), timeout=5)
    try:
        with pytest.raises(BusinessError) as caught:
            await service.ask(session_id, DEMO_USER_ID, "第二条问题", "request-second")
        assert caught.value.code == "session_busy"
        assert len(business.get_session(session_id, DEMO_USER_ID)["messages"]) == 1
    finally:
        release.set()
        await first
    assert len(provider.step_history) == 1


async def test_reopened_async_checkpoint_and_business_store_keep_multiturn_history(settings, tmp_path):
    business = BusinessStore(tmp_path / "restart-business")
    business.initialize()
    session_id = business.create_session(DEMO_USER_ID)["session_id"]
    checkpoint_path = str(tmp_path / "restart-checkpoint.sqlite3")
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        first = AgentService(settings, ScriptedAgentProvider(), EmptyKnowledge(), business, saver)
        await first.ask(session_id, DEMO_USER_ID, "VPN-403 设备未登记", "request-first")

    reopened_business = BusinessStore(tmp_path / "restart-business")
    reopened_business.initialize()
    second_provider = ScriptedAgentProvider()
    async with AsyncSqliteSaver.from_conn_string(checkpoint_path) as saver:
        second = AgentService(settings, second_provider, EmptyKnowledge(), reopened_business, saver)
        previous = await second.graph.aget_state({"configurable": {"thread_id": session_id}})
        assert previous.values["question"] == "VPN-403 设备未登记"
        await second.ask(session_id, DEMO_USER_ID, "还是不行，该联系谁？", "request-second")

    humans = [item.content for item in second_provider.step_history[0] if isinstance(item, HumanMessage)]
    assert humans == ["VPN-403 设备未登记", "还是不行，该联系谁？"]
    assert len(reopened_business.get_session(session_id, DEMO_USER_ID)["messages"]) == 4


@pytest.mark.parametrize("extra", [{"priority": "critical"}, {"user_id": "admin"}, {"confirm": True}])
def test_model_cannot_set_ticket_authority_fields(extra):
    with pytest.raises(ValidationError):
        TicketProposal.model_validate({
            "title": "VPN 设备未登记", "description": "VPN-403 连接持续失败", "category": "vpn", **extra
        })


def test_triage_rejects_extra_authority_fields_and_unsupported_category():
    valid = {
        "category": "vpn", "intent": "troubleshooting", "priority": "normal",
        "missing_fields": [], "needs_info": False,
    }
    for invalid in (
        {**valid, "user_id": "admin"},
        {**valid, "category": "grant_admin_permission"},
        {**valid, "needs_info": True},
        {**valid, "needs_info": "false"},
        {**valid, "missing_fields": ["具体错误码"]},
    ):
        with pytest.raises(ValidationError):
            Triage.model_validate(invalid)

