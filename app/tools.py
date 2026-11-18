"""Exactly three read-only tools. Caller identity is injected outside model arguments."""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.business import BusinessError, BusinessStore
from app.errors import ITDeskError
from app.retrieval.store import KnowledgeStore


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def meaningful_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("query 不能为空")
        return value.strip()


class ServiceArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    service: Literal["vpn", "email", "printing"]


class TicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ticket_id: str = Field(min_length=1, max_length=80, pattern=r"^IT-[A-Za-z0-9]+$")


_ARG_TYPES = {
    "search_knowledge": SearchArgs,
    "get_service_status": ServiceArgs,
    "get_ticket_status": TicketArgs,
}
_DESCRIPTIONS = {
    "search_knowledge": "检索模拟IT手册，返回原文片段及可引用的chunk_id；只读，不执行排障操作。",
    "get_service_status": "查询模拟VPN、邮箱或打印服务状态；固定演示快照，不代表真实企业实时状态。",
    "get_ticket_status": "查询当前演示用户的工单状态；无需且不允许传入用户身份。",
}
TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name, "description": _DESCRIPTIONS[name], "parameters": model.model_json_schema(),
        },
    }
    for name, model in _ARG_TYPES.items()
]

SERVICE_FIXTURES = {
    "vpn": {"service": "vpn", "status": "operational", "summary": "模拟VPN网关运行正常。"},
    "email": {"service": "email", "status": "degraded", "summary": "模拟邮件投递延迟，服务台正在排查。"},
    "printing": {"service": "printing", "status": "operational", "summary": "模拟公共打印队列运行正常。"},
}


class ToolExecutor:
    def __init__(self, store: BusinessStore, knowledge_store: KnowledgeStore):
        self.store, self.knowledge_store = store, knowledge_store

    async def execute(self, name: str, args: dict, user_id: str) -> dict:
        if name not in _ARG_TYPES:
            return {"ok": False, "error": {"code": "unknown_tool", "message": "不允许调用此工具。"}}
        try:
            parsed = _ARG_TYPES[name].model_validate(args)
        except ValidationError:
            return {"ok": False, "error": {"code": "invalid_tool_arguments", "message": "工具参数不合法。"}}
        try:
            async with asyncio.timeout(10):
                if name == "search_knowledge":
                    result = await self.knowledge_store.retrieve(parsed.query)
                    data = {
                        "chunks": [chunk.model_dump() for chunk in result.chunks],
                        "embedding_tokens": result.embedding_tokens,
                    }
                elif name == "get_service_status":
                    data = {
                        **SERVICE_FIXTURES[parsed.service], "is_simulated": True,
                        "observed_at": "2026-11-17T00:00:00Z",
                        "notice": "固定演示快照；不能用于判断当前真实服务是否故障。",
                    }
                else:
                    data = await asyncio.to_thread(self.store.get_ticket, parsed.ticket_id, user_id)
                    data["is_simulated"] = True
                return {"ok": True, "data": data}
        except BusinessError as error:
            return {"ok": False, "error": {"code": error.code, "message": error.message}}
        except ITDeskError as error:
            return {"ok": False, "error": {"code": error.code, "message": error.message}}
        except TimeoutError:
            return {"ok": False, "error": {"code": "tool_timeout", "message": "工具调用超时，请稍后重试。"}}
        except Exception:
            # Do not return raw exception text, which can contain paths, prompts or credentials.
            return {
                "ok": False,
                "error": {"code": "tool_failed", "message": "工具暂时不可用，请联系人工支持。"},
            }
