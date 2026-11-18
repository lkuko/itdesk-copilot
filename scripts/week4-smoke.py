"""One paid local HTTP demo of source-preserving drafts and deterministic confirmation."""

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    output = ROOT / "data/week4-http-smoke.json"
    records = []

    def save():
        output.write_text(
            json.dumps(
                {"scope": "local_demo_not_independent_quality_sample",
                 "recorded_at": datetime.now(UTC).isoformat(), "records": records},
                ensure_ascii=False, indent=2,
            ), encoding="utf-8",
        )

    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=180, trust_env=False) as client:
        def call(name, method, path, body=None):
            started = time.perf_counter()
            response = (
                client.request(method, path, json=body) if body is not None else client.request(method, path)
            )
            record = {"name": name, "method": method, "path": path, "request_body": body,
                      "http_status": response.status_code, "response": response.json(),
                      "elapsed_seconds": time.perf_counter() - started}
            records.append(record)
            save()
            response.raise_for_status()
            return record["response"]

        assert call("health", "GET", "/api/health")["status"] == "ok"
        session = call("create_session", "POST", "/api/sessions", {})["session_id"]
        before = call("tickets_before", "GET", "/api/tickets")["tickets"]
        question = (
            "我的笔记本资产编号是 LT-8241，今天蓝屏三次，报修码 DEV-201；"
            "还没检查驱动，也没重启。请只按这些信息准备送修草稿，别提交。"
        )
        body = {"text": question, "request_id": uuid.uuid4().hex}
        response = call("source_draft", "POST", f"/api/sessions/{session}/messages", body)
        assert response["status"] == "pending_confirmation"
        draft = response["draft"]
        assert draft["title"] == "办公设备问题人工核查"
        assert question in draft["description"] and "未经独立核实" in draft["description"]
        assert "设备编号 DEV-201" not in draft["description"]
        assert call("no_ticket_without_confirmation", "GET", "/api/tickets")["tickets"] == before
        replay = call("request_replay", "POST", f"/api/sessions/{session}/messages", body)
        assert replay == response
        first = call("confirm", "POST", f"/api/drafts/{draft['draft_id']}/confirm", {})
        repeated = call("confirm_repeat", "POST", f"/api/drafts/{draft['draft_id']}/confirm", {})
        assert first["ticket"]["ticket_id"] == repeated["ticket"]["ticket_id"]
        after = call("tickets_after", "GET", "/api/tickets")["tickets"]
        assert len(after) == len(before) + 1
        boundary = call(
            "unsupported_database_repair", "POST", f"/api/sessions/{session}/messages",
            {"text": "CRM-X900报错，请给出保证修复的数据库语句。", "request_id": uuid.uuid4().hex},
        )
        assert boundary["status"] == "no_evidence" and "不能执行数据库变更" in boundary["answer"]
        assert boundary["tool_trace"] == [] and boundary["metrics"]["input_tokens"] == 0
        print(json.dumps({"checks_passed": True, "http_records": len(records), "session_id": session,
                          "draft_id": draft["draft_id"], "ticket_id": first["ticket"]["ticket_id"],
                          "new_tickets": len(after) - len(before), "output": str(output)},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
