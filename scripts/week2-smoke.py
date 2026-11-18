"""Paid development smoke against the local running API; never a production benchmark."""

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = []
    output = ROOT / "data/week2-live-smoke.json"
    output.parent.mkdir(exist_ok=True)
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180, trust_env=False) as client:
        client.get("/api/health").raise_for_status()
        session = client.post("/api/sessions", json={}).json()["session_id"]

        def save():
            output.write_text(json.dumps({"scope": "development_demo_not_held_out",
                                         "timestamp": datetime.now(UTC).isoformat(),
                                         "session_id": session, "records": records},
                                        ensure_ascii=False, indent=2), encoding="utf-8")

        def ask(name, question, expected, tool=None):
            request_id = uuid.uuid4().hex
            started = time.perf_counter()
            response = client.post(f"/api/sessions/{session}/messages",
                                   json={"question": question, "request_id": request_id})
            response.raise_for_status()
            result = response.json()
            passed = result["status"] == expected and (
                not tool or any(t["name"] == tool for t in result["tool_trace"])
            )
            record = {"name": name, "question": question, "expected_status": expected,
                      "required_tool": tool, "passed": passed, "seconds": time.perf_counter() - started,
                      "result": result}
            records.append(record)
            save()
            print(json.dumps({"case": name, "status": result["status"], "passed": passed,
                              "tools": [t["name"] for t in result["tool_trace"]],
                              "seconds": round(record["seconds"], 2)}, ensure_ascii=False), flush=True)
            return result, request_id

        ask("needs_info", "帮我处理一下", "needs_info")
        ask("knowledge", "VPN-403 设备未授权，应该怎么排查？", "answered", "search_knowledge")
        ask("followup_service", "那这个服务在模拟系统中的状态正常吗？", "answered", "get_service_status")
        ask("own_ticket", "查询我的模拟工单 IT-1001 的状态", "answered", "get_ticket_status")
        ask("degraded_service", "现在模拟邮箱服务有故障吗？", "answered", "get_service_status")
        foreign, _ = ask("foreign_ticket", "查询工单 IT-2001 的状态", "no_evidence", "get_ticket_status")
        assert "其他用户的邮箱工单" not in foreign["answer"]
        ask("no_evidence", "公司是否提供住房补贴？仅回答这个新问题，不要沿用前面的话题。", "no_evidence")
        before = len(client.get("/api/tickets").json()["tickets"])
        question = "VPN-403 按手册检查后仍失败，请帮我准备一张工单草稿，交给人工核对设备登记。"
        draft, request_id = ask("ticket_draft", question, "pending_confirmation")
        assert len(client.get("/api/tickets").json()["tickets"]) == before
        replay = client.post(f"/api/sessions/{session}/messages",
                             json={"question": question, "request_id": request_id}).json()
        assert replay["draft"]["draft_id"] == draft["draft"]["draft_id"]
        draft_id = draft["draft"]["draft_id"]
        confirmed = client.post(f"/api/drafts/{draft_id}/confirm", json={})
        confirmed.raise_for_status()
        ticket = confirmed.json()["ticket"]["ticket_id"]
        repeated = client.post(f"/api/drafts/{draft_id}/confirm", json={}).json()
        assert repeated["ticket"]["ticket_id"] == ticket
        assert len(client.get("/api/tickets").json()["tickets"]) == before + 1
        records.append({"name": "confirm_idempotency", "passed": True, "ticket_id": ticket})
        cancelled, _ = ask("cancel_draft", "打印机仍然不能连接，请为这个打印问题准备人工工单草稿。",
                           "pending_confirmation")
        draft_id = cancelled["draft"]["draft_id"]
        client.post(f"/api/drafts/{draft_id}/cancel", json={}).raise_for_status()
        assert client.post(f"/api/drafts/{draft_id}/confirm", json={}).status_code == 409
        records.append({"name": "cancel_blocks_confirmation", "passed": True})
        save()
    if not all(row["passed"] for row in records):
        raise SystemExit("Some development cases failed; inspect data/week2-live-smoke.json")
    print(f"Passed {len(records)} development checks; saved {output}", flush=True)


if __name__ == "__main__":
    main()
