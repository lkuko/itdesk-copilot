# React 与 FastAPI 联调契约

后端继续运行在 `http://127.0.0.1:8000`，React 开发服务使用 5173 端口。路径和业务语义保持现有 `/api` 协议，固定身份仍由后端注入；前端不提交 `user_id`、草稿状态或工单优先级。

## 浏览器 Origin 配置

`app/config.py::Settings.cors_origins` 默认只允许 `http://localhost:5173` 和 `http://127.0.0.1:5173`。`app/main.py::create_app` 使用 `CORSMiddleware`，允许 GET、POST、PATCH、DELETE，以及 Content-Type 请求头，不启用跨域 Cookie 凭证。

在项目 `.env` 或启动后端的 PowerShell 中设置 **JSON 数组**可替换默认值；重新启动后端生效：

```powershell
$env:ITDESK_CORS_ORIGINS = '["http://localhost:5173","http://127.0.0.1:5173"]'
powershell -ExecutionPolicy Bypass -File .\scripts\start-api.ps1
```

空数组 `[]` 关闭跨域许可。只接受 HTTP(S) Origin，不接受通配符、凭证、路径、查询或片段。尾部斜杠会移除，重复项合并。不要把模型服务地址或 API Key 放入此列表。

不允许的浏览器 Origin 的预检返回 400，普通响应不带 `Access-Control-Allow-Origin`，浏览器因此不能向脚本开放响应。**CORS 不是认证或网络访问控制**：curl、同源请求等不会被它拦截，当前服务仍只绑定本机且没有真实登录。

## 业务接口

| 接口 | 输入与用途 | 成功结果 |
|---|---|---|
| `GET /api/health` | 无；只检查本地配置与索引，不调用模型 | `status`、`checks`、`models`、`index`、`agent` |
| `POST /api/sessions` | `{}` | 新会话，HTTP 201 |
| `GET /api/sessions?limit=10&offset=0` | limit 1–100，offset 非负 | `{sessions: [...]}`，无总数；不足一页时禁用下一页 |
| `GET /api/sessions/{id}` | 会话编号 | 会话字段、`messages`、`drafts` |
| `PATCH /api/sessions/{id}` | `{title}`，1–80 字 | 更新后的会话 |
| `DELETE /api/sessions/{id}` | `{}`；页面先明确确认 | 删除结果；已确认工单和审计保留 |
| `POST /api/sessions/{id}/messages` | `{question, request_id}`；`text` 仍可作为 question 别名 | Agent 结果：状态、回答、引用、分类、草稿、工具轨迹、指标等 |
| `GET /api/drafts/{id}` | 草稿编号 | 已保存草稿 |
| `PATCH /api/drafts/{id}` | `{title, description, category}`；category 为 vpn/email/device | `{status: "draft_updated", draft: ...}`；保存不会创建工单 |
| `POST /api/drafts/{id}/confirm` | `{}`；明确按钮确认 | `{status: "ticket_created", ticket: ..., draft: ...}`；重复确认返回相同工单 |
| `POST /api/drafts/{id}/cancel` | `{}` | 取消结果；取消后不能确认 |
| `GET /api/tickets?limit=30&offset=0` | limit 1–100，offset 非负 | `{tickets: [...]}` |
| `GET /api/tickets/{id}` | 当前演示用户可见的工单编号 | 工单记录；外部用户记录与不存在记录统一不可查询 |

消息状态为 `answered / needs_info / pending_confirmation / no_evidence / failed`。`POST /api/ask` 是早期无会话兼容接口，响应 `AskResponse` 比会话 Agent 结果更窄，React 主聊天不应使用它。会话消息响应目前未声明完整的 OpenAPI `response_model`；前端类型以 Agent 实际结果和对应 API 测试为准，不直接套用旧 `AskResponse`。

问题去除首尾空白后需 1–2000 字。`request_id` 为 8–64 位字母、数字、下划线或短横线。超时或失联后先刷新会话，检查服务端已保存结果；结果未知时重试原请求须保留原问题和原编号，已完成请求会重放持久化结果。同编号换问题会被拒绝；会话忙时需等待。若返回 `409 request_interrupted`，表示旧编号已有用户消息但没有完成结果，不能重放执行；页面应明确提示，由用户选择“以新请求重新提交”，生成新编号后发送原问题，不得自动换编号重复执行。一个会话同时只允许一个 Agent 请求。

## 错误与确认边界

- 参数校验的 422 使用 `detail` 数组：条目包含 `loc/msg/type`，不会回显输入原文；不能假设所有错误都是同一种对象。
- 业务错误使用 `detail: {code, message}`，例如敏感输入 422、资源不可见 404、会话忙或请求冲突 409。
- 存储不可用使用 503，`detail.code=storage_unavailable`；前端不得显示创建成功。
- Agent 已执行但无法给出结论时，HTTP 可能为 200，同时 `status=failed` 且带 `error`；HTTP 成功不等于业务成功。
- 工单确认只发送 `{}`；后端读取已保存草稿并执行事务，前端不能在确认请求中替换草稿内容。
- 暂存、重试、引用展开和界面设置不赋予模型写权限；前端显示偏好由浏览器保存，不增加后端偏好接口。

测试见 `tests/test_cors.py`、`tests/test_week2_api.py` 和 `tests/test_frontend_business.py`；这些测试使用本地模型替身，不产生远端调用。

## 知识库实验与机器人

| 接口 | 输入 | 返回 |
|---|---|---|
| `GET /api/knowledge` | 无 | `index`、模型、窗口与重叠、文档编号和版本、片段数 |
| `GET /api/knowledge/documents/{doc_id}` | 目录中的文档编号 | 归一化原文，生产切法的片段和 `[start, end)` 字符位置 |
| `GET /api/knowledge/workspace` | 无 | 生效配置、可选模型、索引状态、已导入文档编号和版本号 |
| `POST /api/knowledge/workspace/preview` | `profile` 与 `.md` 或 `.txt` 文件内容 | 归一化原文、分段大小及字符位置。预览不调用远程模型 |
| `POST /api/knowledge/workspace/apply` | `profile`、要导入的文件、要移除的编号和当前 `revision` | 发布后的状态与是否重建。版本冲突返回 409 |
| `POST /api/knowledge/workspace/search` | JSON `query` 1–500 字 | 当前问答配置下的命中、余弦分数、重排分数和重排状态 |
| `POST /api/knowledge/compare` | JSON `query` 1–500 字，`top_k` 1–10 | 同一语料与模型的两组向量排名、片段和余弦分数。文档和查询 Token 按整轮计量 |
| `POST /api/bot/telegram` | 官方私聊 update 和 webhook secret header | Telegram 支持的 `sendMessage` 响应。未配置时为 404 |

知识库查看不会请求模型。实验接口有语料和片段数限制，默认只允许一个并发实验，Embedding 比较受 75 秒超时约束。查询已付费用量以实际供应商记录为准。Telegram 入口在本地用合成 update 验证，默认关闭。资料与接口细节见[知识库实验与验收](knowledge-lab.md)。

工作台用 SQLite 控制并发写入。新文档或新 Embedding 先在独立版本构建，索引可用才发布。移除只作用于导入的文档，旧版本仍保留在本机。余弦阈值只过滤向量候选，完整错误码及其相邻证据保留原有优先级。重排失败返回 `rerank_status=fallback`，结果沿用该次检索的初始顺序。模型名称来自后端配置。[工作台源码](../app/retrieval/workspace.py) · [检索和重排](../app/retrieval/store.py)
