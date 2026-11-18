"""Local demo business records. Checkpoints are never the source of ticket truth.

Each operation opens its own SQLite connection, so callers can use asyncio.to_thread.
The injected owner is a demonstration boundary, not enterprise authentication.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.input_safety import sensitive_input_reason

DEMO_USER_ID = "demo-user"
OTHER_USER_ID = "other-demo-user"


class BusinessError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code, self.code, self.message = status_code, code, message
        super().__init__(message)


class DraftFields(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=2000)
    category: str

    @field_validator("title", "description")
    @classmethod
    def meaningful(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("工单字段不能为空")
        return value.strip()

    @field_validator("category")
    @classmethod
    def supported_category(cls, value: str) -> str:
        if value not in {"vpn", "email", "device"}:
            raise ValueError("不支持的工单分类")
        return value


class SessionRename(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=1, max_length=80)

    @field_validator("title")
    @classmethod
    def meaningful(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("会话标题不能为空")
        return value.strip()


def _safe_input(*texts: str) -> None:
    for text in texts:
        reason = sensitive_input_reason(text)
        if reason:
            raise BusinessError(422, "sensitive_input", reason)


class BusinessStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "business.sqlite3"

    @contextmanager
    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS schema_meta (name TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS messages (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, message_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id),
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                    content TEXT NOT NULL, result_json TEXT, request_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS message_request_dedup
                    ON messages(session_id,request_id)
                    WHERE role='user' AND request_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(session_id), user_id TEXT NOT NULL,
                    title TEXT NOT NULL, description TEXT NOT NULL, category TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal' CHECK(priority='normal'),
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','confirmed','cancelled')),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    draft_id TEXT UNIQUE REFERENCES drafts(draft_id), title TEXT NOT NULL,
                    description TEXT NOT NULL, category TEXT NOT NULL,
                    priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
                    action TEXT NOT NULL, entity_id TEXT NOT NULL, details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)
            # A persistent marker means restarting cannot resurrect removed fixture records.
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM schema_meta WHERE name='seed_v1'").fetchone():
                db.executemany(
                    """INSERT INTO tickets(ticket_id,user_id,title,description,category,status)
                        VALUES(?,?,?,?,?,?)""",
                    [
                        ("IT-1001", DEMO_USER_ID, "VPN 设备登记检查", "演示工单：等待人工核对设备登记。",
                         "vpn", "in_progress"),
                        ("IT-1002", DEMO_USER_ID, "打印机队列异常", "演示工单：等待服务台排查打印队列。",
                         "device", "open"),
                        ("IT-2001", OTHER_USER_ID, "其他用户的邮箱工单", "不可返回给当前演示用户。",
                         "email", "open"),
                    ],
                )
                db.execute("INSERT INTO schema_meta(name,value) VALUES('seed_v1','done')")

    @staticmethod
    def _page(limit: int, offset: int):
        if type(limit) is not int or type(offset) is not int or not 1 <= limit <= 100 or offset < 0:
            raise BusinessError(422, "invalid_pagination", "limit 必须为 1–100，offset 必须为非负整数。")

    @staticmethod
    def _session(db, session_id: str, user_id: str):
        row = db.execute(
            "SELECT * FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id)
        ).fetchone()
        if row is None:
            raise BusinessError(404, "session_not_found", "会话不存在。")
        return row

    @staticmethod
    def _draft(db, draft_id: str, user_id: str):
        row = db.execute(
            "SELECT * FROM drafts WHERE draft_id=? AND user_id=?", (draft_id, user_id)
        ).fetchone()
        if row is None:
            raise BusinessError(404, "draft_not_found", "草稿不存在。")
        return row

    @staticmethod
    def _message(row):
        item = dict(row)
        item.pop("sequence", None)
        result_json = item.pop("result_json")
        item["result"] = json.loads(result_json) if result_json else None
        return item

    @staticmethod
    def _audit(db, user_id: str, action: str, entity_id: str, details: dict):
        db.execute(
            "INSERT INTO audit(user_id,action,entity_id,details_json) VALUES(?,?,?,?)",
            (user_id, action, entity_id, json.dumps(details, ensure_ascii=False)),
        )

    def create_session(self, user_id: str = DEMO_USER_ID) -> dict:
        session_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions(session_id,user_id,title) VALUES(?,?,?)",
                (session_id, user_id, "新会话"),
            )
            return dict(self._session(db, session_id, user_id))

    def list_sessions(self, user_id: str = DEMO_USER_ID, limit: int = 30, offset: int = 0) -> list[dict]:
        self._page(limit, offset)
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM sessions WHERE user_id=?
                    ORDER BY updated_at DESC,rowid DESC LIMIT ? OFFSET ?""", (user_id, limit, offset)
            )]

    def get_session(self, session_id: str, user_id: str = DEMO_USER_ID) -> dict:
        with self._connect() as db:
            result = dict(self._session(db, session_id, user_id))
            result["messages"] = [self._message(row) for row in db.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY sequence", (session_id,)
            )]
            result["drafts"] = [dict(row) for row in db.execute(
                """SELECT d.*,t.ticket_id FROM drafts d LEFT JOIN tickets t
                    ON t.draft_id=d.draft_id AND t.user_id=d.user_id
                    WHERE d.session_id=? AND d.user_id=? ORDER BY d.rowid""",
                (session_id, user_id),
            )]
            return result

    def rename_session(self, session_id: str, user_id: str, title: str) -> dict:
        fields = SessionRename(title=title)
        _safe_input(fields.title)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._session(db, session_id, user_id)
            db.execute(
                "UPDATE sessions SET title=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (fields.title, session_id),
            )
            self._audit(db, user_id, "session_renamed", session_id, {
                "previous_title": prior["title"], "title": fields.title,
            })
            return dict(self._session(db, session_id, user_id))

    def delete_session(self, session_id: str, user_id: str) -> dict:
        """Remove local conversation content while preserving already created tickets.

        Existing ticket/draft relationships are copied to the deletion audit before
        nullable ticket foreign keys are detached. Other audit records remain.
        """
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id, user_id)
            drafts = [row["draft_id"] for row in db.execute(
                "SELECT draft_id FROM drafts WHERE session_id=? AND user_id=?", (session_id, user_id)
            )]
            links = [dict(row) for row in db.execute(
                """SELECT ticket_id,draft_id FROM tickets WHERE user_id=? AND draft_id IN
                    (SELECT draft_id FROM drafts WHERE session_id=? AND user_id=?)""",
                (user_id, session_id, user_id),
            )]
            db.execute(
                """UPDATE tickets SET draft_id=NULL WHERE user_id=? AND draft_id IN
                    (SELECT draft_id FROM drafts WHERE session_id=? AND user_id=?)""",
                (user_id, session_id, user_id),
            )
            db.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            db.execute("DELETE FROM drafts WHERE session_id=? AND user_id=?", (session_id, user_id))
            self._audit(db, user_id, "session_deleted", session_id, {
                "deleted_draft_ids": drafts, "preserved_tickets": links,
            })
            db.execute("DELETE FROM sessions WHERE session_id=? AND user_id=?", (session_id, user_id))
            return {"status": "session_deleted", "session_id": session_id}

    def append_message(
        self, session_id: str, user_id: str, role: str, content: str,
        result: dict | None = None, request_id: str | None = None,
    ) -> dict:
        if role not in {"user", "assistant"} or not isinstance(content, str) or not content.strip():
            raise BusinessError(422, "invalid_message", "消息角色或内容不合法。")
        message_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            session = self._session(db, session_id, user_id)
            try:
                db.execute(
                    """INSERT INTO messages(message_id,session_id,role,content,result_json,request_id)
                        VALUES(?,?,?,?,?,?)""",
                    (message_id, session_id, role, content,
                     json.dumps(result, ensure_ascii=False) if result is not None else None, request_id),
                )
            except sqlite3.IntegrityError:
                raise BusinessError(409, "duplicate_request", "该消息已接收，请刷新会话查看结果。") from None
            title = content[:40] if role == "user" and session["title"] == "新会话" else session["title"]
            db.execute(
                "UPDATE sessions SET title=?,updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (title, session_id),
            )
            return self._message(db.execute(
                "SELECT * FROM messages WHERE message_id=?", (message_id,)
            ).fetchone())

    def create_draft(
        self, session_id: str, user_id: str, title: str, description: str, category: str,
    ) -> dict:
        fields = DraftFields(title=title, description=description, category=category)
        draft_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id, user_id)
            db.execute(
                """INSERT INTO drafts(draft_id,session_id,user_id,title,description,category)
                    VALUES(?,?,?,?,?,?)""",
                (draft_id, session_id, user_id, fields.title, fields.description, fields.category),
            )
            self._audit(db, user_id, "draft_created", draft_id, {"session_id": session_id})
            db.execute(
                "UPDATE sessions SET updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,)
            )
            return dict(self._draft(db, draft_id, user_id))

    def get_request(self, session_id: str, user_id: str, request_id: str) -> dict | None:
        with self._connect() as db:
            self._session(db, session_id, user_id)
            question = db.execute(
                """SELECT content FROM messages WHERE session_id=? AND request_id=? AND role='user'""",
                (session_id, request_id),
            ).fetchone()
            if question is None:
                return None
            answer = db.execute(
                """SELECT result_json FROM messages
                    WHERE session_id=? AND request_id=? AND role='assistant'
                    ORDER BY sequence DESC LIMIT 1""",
                (session_id, request_id),
            ).fetchone()
            return {
                "question": question["content"],
                "result": json.loads(answer["result_json"]) if answer and answer["result_json"] else None,
            }

    def finish_turn(
        self, session_id: str, user_id: str, request_id: str, result: dict, proposal: dict | None = None,
    ) -> dict:
        """Commit the answer and optional draft together, replaying an already committed turn.

        A client request ID cannot produce a second draft even after a connection timeout.
        Failed commits roll back both the draft/audit and assistant message.
        """
        fields = DraftFields.model_validate(proposal) if proposal is not None else None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._session(db, session_id, user_id)
            question = db.execute(
                "SELECT 1 FROM messages WHERE session_id=? AND request_id=? AND role='user'",
                (session_id, request_id),
            ).fetchone()
            if question is None:
                raise BusinessError(409, "request_not_started", "请先保存本轮用户消息。")
            prior = db.execute(
                """SELECT result_json FROM messages WHERE session_id=? AND request_id=?
                    AND role='assistant' ORDER BY sequence DESC LIMIT 1""",
                (session_id, request_id),
            ).fetchone()
            if prior:
                return json.loads(prior["result_json"])
            saved = dict(result)
            if fields is not None:
                draft_id = uuid.uuid4().hex
                db.execute(
                    """INSERT INTO drafts(draft_id,session_id,user_id,title,description,category)
                        VALUES(?,?,?,?,?,?)""",
                    (draft_id, session_id, user_id, fields.title, fields.description, fields.category),
                )
                self._audit(db, user_id, "draft_created", draft_id, {
                    "session_id": session_id, "request_id": request_id,
                })
                saved["draft"] = dict(self._draft(db, draft_id, user_id))
            else:
                saved["draft"] = None
            answer = saved.get("answer")
            if not isinstance(answer, str) or not answer.strip():
                raise BusinessError(422, "invalid_answer", "助手消息内容不合法。")
            db.execute(
                """INSERT INTO messages(message_id,session_id,role,content,result_json,request_id)
                    VALUES(?,?,'assistant',?,?,?)""",
                (uuid.uuid4().hex, session_id, answer, json.dumps(saved, ensure_ascii=False), request_id),
            )
            db.execute(
                "UPDATE sessions SET updated_at=CURRENT_TIMESTAMP WHERE session_id=?", (session_id,)
            )
            return saved

    def get_draft(self, draft_id: str, user_id: str = DEMO_USER_ID) -> dict:
        with self._connect() as db:
            return dict(self._draft(db, draft_id, user_id))

    def update_draft(
        self, draft_id: str, user_id: str, title: str, description: str, category: str,
    ) -> dict:
        fields = DraftFields(title=title, description=description, category=category)
        _safe_input(fields.title, fields.description)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = self._draft(db, draft_id, user_id)
            if prior["status"] != "pending":
                raise BusinessError(409, "draft_not_pending", "仅待确认草稿可以修改，请刷新查看最新状态。")
            db.execute(
                """UPDATE drafts SET title=?,description=?,category=?,updated_at=CURRENT_TIMESTAMP
                    WHERE draft_id=? AND user_id=?""",
                (fields.title, fields.description, fields.category, draft_id, user_id),
            )
            # Do not duplicate report text in audit storage; it remains in the draft.
            changed = [name for name in ("title", "description", "category")
                       if prior[name] != getattr(fields, name)]
            self._audit(db, user_id, "draft_edited", draft_id, {
                "session_id": prior["session_id"], "changed_fields": changed,
            })
            db.execute(
                "UPDATE sessions SET updated_at=CURRENT_TIMESTAMP WHERE session_id=?",
                (prior["session_id"],),
            )
            return {"status": "draft_updated", "draft": dict(self._draft(db, draft_id, user_id))}

    def confirm_draft(self, draft_id: str, user_id: str = DEMO_USER_ID) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = self._draft(db, draft_id, user_id)
            if draft["status"] == "cancelled":
                raise BusinessError(409, "draft_cancelled", "草稿已取消，不能提交。")
            ticket = db.execute(
                "SELECT * FROM tickets WHERE draft_id=? AND user_id=?", (draft_id, user_id)
            ).fetchone()
            if draft["status"] == "confirmed":
                if ticket is None:
                    raise BusinessError(409, "inconsistent_draft", "草稿状态不一致，请联系人工检查。")
                return {"ticket": dict(ticket), "draft": dict(draft)}
            # The client can only identify a persisted draft; every ticket field comes from that row.
            ticket_id = f"IT-{uuid.uuid4().hex[:12].upper()}"
            db.execute(
                """INSERT INTO tickets(ticket_id,user_id,draft_id,title,description,category,priority)
                    VALUES(?,?,?,?,?,?,?)""",
                (ticket_id, user_id, draft_id, draft["title"], draft["description"],
                 draft["category"], draft["priority"]),
            )
            db.execute(
                "UPDATE drafts SET status='confirmed',updated_at=CURRENT_TIMESTAMP WHERE draft_id=?",
                (draft_id,),
            )
            self._audit(db, user_id, "ticket_confirmed", draft_id, {"ticket_id": ticket_id})
            ticket = db.execute("SELECT * FROM tickets WHERE ticket_id=?", (ticket_id,)).fetchone()
            return {"ticket": dict(ticket), "draft": dict(self._draft(db, draft_id, user_id))}

    def cancel_draft(self, draft_id: str, user_id: str = DEMO_USER_ID) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            draft = self._draft(db, draft_id, user_id)
            if draft["status"] == "confirmed":
                raise BusinessError(409, "draft_confirmed", "草稿已提交，不能取消；请联系人工处理工单。")
            if draft["status"] == "pending":
                db.execute(
                    "UPDATE drafts SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE draft_id=?",
                    (draft_id,),
                )
                self._audit(db, user_id, "draft_cancelled", draft_id, {})
            return {"draft": dict(self._draft(db, draft_id, user_id))}

    def list_tickets(self, user_id: str = DEMO_USER_ID, limit: int = 30, offset: int = 0) -> list[dict]:
        self._page(limit, offset)
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT * FROM tickets WHERE user_id=?
                    ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?""", (user_id, limit, offset)
            )]

    def get_ticket(self, ticket_id: str, user_id: str = DEMO_USER_ID) -> dict:
        with self._connect() as db:
            ticket = db.execute(
                "SELECT * FROM tickets WHERE ticket_id=? AND user_id=?", (ticket_id, user_id)
            ).fetchone()
            if ticket is None:
                # Missing and foreign records deliberately have indistinguishable responses.
                raise BusinessError(404, "ticket_not_found", "工单不存在或当前用户不可查看。")
            return dict(ticket)
