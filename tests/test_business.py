"""Business boundaries: ownership, explicit confirmation and atomic durable writes."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.business import BusinessError, BusinessStore


@pytest.fixture
def business(tmp_path):
    store = BusinessStore(tmp_path / "business-data")
    store.initialize()
    return store


def draft_for(store, user="alice"):
    session = store.create_session(user)
    draft = store.create_draft(
        session["session_id"], user, "VPN 连不上", "VPN-403；已核对账号，需 IT 检查设备登记。", "vpn"
    )
    return session, draft


def test_draft_requires_confirmation_and_confirmation_is_idempotent(business):
    before = len(business.list_tickets("alice"))
    session, draft = draft_for(business)
    assert len(business.list_tickets("alice")) == before
    assert draft["status"] == "pending"

    first = business.confirm_draft(draft["draft_id"], "alice")
    repeated = business.confirm_draft(draft["draft_id"], "alice")

    assert first["ticket"]["ticket_id"] == repeated["ticket"]["ticket_id"]
    assert first["ticket"]["title"] == draft["title"]
    assert first["ticket"]["description"] == draft["description"]
    assert first["draft"]["status"] == "confirmed"
    assert len(business.list_tickets("alice")) == before + 1
    assert business.get_session(session["session_id"], "alice")["drafts"][0]["status"] == "confirmed"


def test_cancel_prevents_creation_and_is_idempotent(business):
    _, draft = draft_for(business)
    before = len(business.list_tickets("alice"))

    first = business.cancel_draft(draft["draft_id"], "alice")
    repeated = business.cancel_draft(draft["draft_id"], "alice")

    assert first["draft"]["status"] == repeated["draft"]["status"] == "cancelled"
    with pytest.raises(BusinessError) as caught:
        business.confirm_draft(draft["draft_id"], "alice")
    assert caught.value.status_code == 409
    assert len(business.list_tickets("alice")) == before


def test_confirmed_ticket_cannot_be_cancelled_as_pending_draft(business):
    _, draft = draft_for(business)
    ticket = business.confirm_draft(draft["draft_id"], "alice")["ticket"]

    with pytest.raises(BusinessError) as caught:
        business.cancel_draft(draft["draft_id"], "alice")

    assert caught.value.status_code == 409
    assert business.get_ticket(ticket["ticket_id"], "alice")["ticket_id"] == ticket["ticket_id"]


def test_foreign_and_unknown_resources_have_same_public_failure(business):
    session, draft = draft_for(business, "owner")
    ticket = business.confirm_draft(draft["draft_id"], "owner")["ticket"]
    lookups = [
        (business.get_session, session["session_id"]),
        (business.get_draft, draft["draft_id"]),
        (business.confirm_draft, draft["draft_id"]),
        (business.cancel_draft, draft["draft_id"]),
        (business.get_ticket, ticket["ticket_id"]),
    ]
    for lookup, identifier in lookups:
        errors = []
        for requested in (identifier, "does-not-exist"):
            with pytest.raises(BusinessError) as caught:
                lookup(requested, "stranger")
            error = caught.value
            errors.append((error.status_code, error.code, error.message))
        assert errors[0] == errors[1]
        assert errors[0][0] == 404
    assert business.list_sessions("stranger") == []
    assert business.list_tickets("stranger") == []


def test_sessions_and_messages_survive_repository_reopen(business, tmp_path):
    session = business.create_session("alice")
    business.append_message(session["session_id"], "alice", "user", "VPN 出错了")
    business.append_message(
        session["session_id"], "alice", "assistant", "请提供错误码",
        {"status": "answered", "answer": "请提供错误码"},
    )

    reopened = BusinessStore(tmp_path / "business-data")
    reopened.initialize()
    restored = reopened.get_session(session["session_id"], "alice")

    assert [message["role"] for message in restored["messages"]] == ["user", "assistant"]
    assert restored["messages"][0]["content"] == "VPN 出错了"
    assert restored["messages"][1]["result"]["answer"] == "请提供错误码"


def test_confirmation_rolls_back_ticket_and_draft_if_audit_write_fails(business, tmp_path):
    _, draft = draft_for(business)
    before = len(business.list_tickets("alice"))
    database = next((tmp_path / "business-data").glob("*.sqlite3"))
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TRIGGER reject_audit BEFORE INSERT ON audit "
            "BEGIN SELECT RAISE(ABORT, 'simulated audit disk failure'); END"
        )

    with pytest.raises((sqlite3.Error, BusinessError)):
        business.confirm_draft(draft["draft_id"], "alice")

    assert business.get_draft(draft["draft_id"], "alice")["status"] == "pending"
    assert len(business.list_tickets("alice")) == before


def test_concurrent_confirmations_commit_exactly_one_ticket_and_audit_record(business):
    _, draft = draft_for(business)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: business.confirm_draft(draft["draft_id"], "alice"), range(4)))

    assert len({result["ticket"]["ticket_id"] for result in results}) == 1
    with sqlite3.connect(business.path) as db:
        count = db.execute(
            "SELECT COUNT(*) FROM audit WHERE entity_id=? AND action='ticket_confirmed'", (draft["draft_id"],)
        ).fetchone()[0]
    assert count == 1


def test_answer_commit_failure_rolls_back_draft_and_audit_together(business):
    session_id = business.create_session("alice")["session_id"]
    business.append_message(session_id, "alice", "user", "需要帮助", request_id="request-1234")
    with sqlite3.connect(business.path) as db:
        db.execute(
            "CREATE TRIGGER reject_answer BEFORE INSERT ON messages WHEN NEW.role='assistant' "
            "BEGIN SELECT RAISE(ABORT, 'simulated answer write failure'); END"
        )

    with pytest.raises((sqlite3.Error, BusinessError)):
        business.finish_turn(
            session_id, "alice", "request-1234", {"answer": "请确认草稿", "status": "pending_confirmation"},
            {"title": "VPN 故障", "description": "VPN-403 持续连接失败。", "category": "vpn"},
        )

    saved = business.get_session(session_id, "alice")
    assert saved["drafts"] == []
    assert len(saved["messages"]) == 1
    assert business.get_request(session_id, "alice", "request-1234")["result"] is None
    with sqlite3.connect(business.path) as db:
        assert db.execute("SELECT COUNT(*) FROM audit WHERE user_id='alice'").fetchone()[0] == 0


def test_reinitialization_does_not_resurrect_removed_fixture_records(business):
    with sqlite3.connect(business.path) as db:
        db.execute("DELETE FROM tickets WHERE ticket_id='IT-1001'")

    business.initialize()

    with pytest.raises(BusinessError) as caught:
        business.get_ticket("IT-1001", "demo-user")
    assert caught.value.status_code == 404

