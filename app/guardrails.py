"""Small deterministic boundaries for search identifiers and proposed ticket facts."""

import re

from pydantic import ValidationError

from app.retrieval.ingestion import CODE_PATTERN, extract_error_codes
from app.tools import SearchArgs

DATABASE_BOUNDARY = (
    "当前助手没有该系统的数据库维修依据，也没有数据库执行工具。"
    "不能提供保证修复的 SQL，不能执行数据库变更。"
    "请由系统负责人核实错误、备份和变更方案；这里可以收集不含凭证的症状供人工核查。"
)
TICKET_NOTICE = (
    "已按你提供的原话整理人工核查草稿，用户陈述尚未独立核实。"
    "请核对描述、设备编号与已尝试步骤；缺失信息不会自动补成事实。"
    "尚未创建工单。请检查内容后点击页面“确认创建”，也可取消。"
)


def requires_database_change(question: str) -> bool:
    """Match operational repair requests; plain SQL/transaction explanations are unaffected."""
    if re.search(r"解释|原理|概念|含义|示例", question) and not re.search(
        r"保证修复|保证恢复|替我执行|帮我执行|请执行|直接执行|实际执行", question
    ):
        return False
    return bool(
        re.search(r"\bSQL\b|数据库|改库", question, re.I)
        and re.search(r"修复|恢复|变更|改库|删库|删表|更新数据|修改数据", question)
        and re.search(r"保证|给|提供|生成|写|执行|运行|帮|请|直接", question)
    )


def grounded_search_args(args: dict, question: str, history: list[dict]) -> dict:
    """Only amend an already valid search; preserve original user identifiers within its limit."""
    try:
        parsed = SearchArgs.model_validate(args)
    except ValidationError:
        return args  # Extra authority fields and malformed arguments must still be rejected.
    codes = extract_error_codes(question)
    if not codes and re.search(r"上面|上述|刚才|这个|那个|还是|仍然|依旧|接下来|按手册|那它", question):
        latest_user = next((row["content"] for row in reversed(history) if row["role"] == "user"), "")
        codes = extract_error_codes(latest_user)
    if not codes:
        return args
    # The model must not replace a newly supplied code with an older or invented identifier.
    query = CODE_PATTERN.sub("", parsed.query)
    identifiers = " ".join(sorted(codes))
    # A user question is <=2000 chars; never displace its identifiers with a long model rewrite.
    remaining = max(0, 2000 - len(identifiers) - 1)
    return {"query": (identifiers + " " + query[:remaining]).strip()}


def redact_report(text: str) -> str:
    """Mask explicit credential labels and common token forms; not general PII detection."""
    text = re.sub(
        r"(?i)((?:API[ _-]?KEY|密码|口令|令牌|验证码|恢复码|password|token|secret)"
        r"\s*(?:是|为|[:：=])\s*)[^\s，。；,;]+",
        r"\1[已遮蔽]",
        text,
    )
    return re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+[A-Za-z0-9._-]+)", "[已遮蔽]", text)


def sourced_ticket(proposal: dict, question: str, history: list[dict]) -> dict:
    """A model may propose a category, but cannot rewrite the persisted reported facts."""
    category = proposal["category"]
    title = {
        "vpn": "VPN问题人工核查", "email": "邮箱问题人工核查", "device": "办公设备问题人工核查"
    }[category]
    current = redact_report(question)
    suffix = "…（原话过长，请在确认前核对完整会话）"
    if len(current) > 1500:
        current = current[: 1500 - len(suffix)] + suffix
    sections = ["用户本轮原话（未经独立核实）：\n" + current]
    previous = [row["content"] for row in history if row["role"] == "user"][-2:]
    if previous:
        sections.append("最近用户原话（仅上下文，不自动视为同一问题或已核实事实）：")
        for text in previous:
            text = redact_report(text)
            remaining = 1950 - len("\n\n".join(sections))
            if remaining <= 30:
                break
            limit = min(180, remaining - 5)
            sections.append(text if len(text) <= limit else text[: limit - 1] + "…")
    return {"title": title, "description": "\n\n".join(sections), "category": category}
