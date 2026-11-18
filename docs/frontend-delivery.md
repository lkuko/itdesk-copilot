# 前端优化交付记录

> 本文记录历史交付。旧源码入口和界面测试请查[原型归档](streamlit-retirement.md)。

后续版本说明：聊天布局及独立显示设置已继续迭代，当前页面、八项设置、保存边界与本轮证据见[聊天界面与显示设置](chat-ui-settings.md)。下文保留前一轮的实现、尺寸、截图和测试快照，不能作为当前设置版重新执行的结果。

日期：2026-11-18。依据用户指定的《ITDesk-Copilot界面优化提示词》，完成 Streamlit 工作台的信息分组、交互反馈与视觉更新。项目继续保持中等难度：单 Agent、三个只读工具、FastAPI、Streamlit、本机模拟数据。未迁移前端框架，未增加企业登录或真实工单集成。

本次不重跑第三/四周的模型质量评测，不覆盖它们的题目、原始结果、评分或源码摘要。第四周核心架构、端到端链路、四组技术九问和业务五问见[设计研究](final-design.md)；本记录补充其后的页面与管理 API 变化。

## 1. 可以直接查看的结果

- 本地工作台：`http://127.0.0.1:8501/`。启动方式及 UI 进程环境开关见 [README](../README.md)。
- [桌面浅色截图](evidence/frontend-desktop.jpg)（1280×800）、[桌面暗色截图](evidence/frontend-dark.jpg)（1280×800）。
- [手机首屏截图](evidence/frontend-mobile.jpg)（390×844）、[手机规则回复与运行记录](evidence/frontend-mobile-response.jpg)。
- [验收摘要](evidence/frontend-checks.json)、[现版源码 SHA-256](evidence/frontend-source-sha256.json)。

首屏输入区上移，推荐问题按三类排列；会话、知识库和历史工单有清楚入口。会话可持久化重命名、切换、删除；草稿可修改后保存，再由用户单独确认创建。示例与转人工引导只预填问题，不会自动向模型发送。

## 2. 逐项验收

原提示词最后共有七条总验收项，以下按九个实现域核对。自动化验证流程和数据状态，浏览器验证真实布局与原生交互；两者不相互替代。

| 实现域 | 已交付行为 | 验证与边界 |
|---|---|---|
| 视觉与布局 | 统一浅灰、白、靛蓝与状态色；280px 侧栏、1040px 主区上限、卡片圆角、聚焦态、150–170ms 轻动效、减少动效媒体规则。 | 桌面实屏及 CSS 核对。小字令牌在浅/暗常用背景上计算对比度均达到 4.5:1；未声称完成全部 WCAG 审计。 |
| 导航与会话 | 单侧栏、最近会话列表、时间、选中态、10条分页、独立滚动；持久化重命名，勾选确认后删除。 | UI/后端测试覆盖恢复、所有者、busy保护、删除与事务失败。桌面刷新按钮底部约671px，底栏顶部约705px，不再遮挡。 |
| 知识库状态 | 加载、未初始化、不可用、部分降级、就绪、重试；展示手册/片段数、索引时间和检查时间。 | 索引时间取真实 manifest 文件 mtime，检查时间单独显示；health 不调用模型，不能证明上游真实能力。 |
| 首屏与历史工单 | 中央输入、三组等宽推荐、历史工单独立页面及空态；中文工单状态、更新时间、描述。 | AppTest覆盖预填及工单视图；桌面/手机实屏。没有附件业务，未增加无法处理的上传入口。 |
| 原生输入与辅助功能 | 空白不能提交；2000字限制、实时计数、Enter发送、Shift+Enter换行、中文发送/侧栏标签与提示；错误使用alert/aria-live。 | 浏览器实际输入、换行、发送并查看回复；输入通过原生aria-label命名，计数通过aria-describedby关联。完整键盘/读屏审计未完成。 |
| 消息与依据 | 用户右侧浅靛蓝气泡、助手白卡；来源手册与片段折叠；工具步骤、实际参数、失败状态、时延/Token及未知费用保留。 | 实际恢复第四周已保存会话，查看引用与工具记录；不改旧响应。来源存在不等于语义支持。 |
| 草稿闭环 | 展示标题、分类、描述、普通优先级、关联说明与本机保存时间；修改→保存仍pending，再确认或取消；恢复后显示已建工单号。 | 测试确认用修改后的持久化字段、重复确认幂等、取消后拒绝确认。编辑是显式保存，不承诺未保存的键入自动落盘。 |
| 异常与敏感输入 | 断线、超时、工具失败、无依据、连接恢复有提示；超时保留request_id，刷新可识别迟完成结果；新输入有限凭证拦截，旧响应递归遮蔽后显示。 | 测试覆盖重试UUID、迟完成、名称/草稿输入清理、错误无凭证回显。检测是有限规则，非完整DLP/PII系统；本机旧数据库未改写或加密。 |
| 框架、主题与响应式 | 默认隐藏内部地址、Deploy、原生菜单与版本号；自定义浅色/暗色/跟随系统，右下帮助；窄屏侧栏可折叠，推荐单列。 | 1280×800与390×844实屏检查；手机document宽390px无横向溢出，帮助未挡输入。用框架自带折叠入口，没有额外造第二套图标轨。 |

独立 localhost 页面没有提示词录屏中宿主的额外窄图标轨或粉色悬浮按钮；未修改 Codex 宿主界面。Deploy 入口已隐藏，没有新增部署模态框，其三栏布局、ESC和遮罩关闭要求不适用。本页设置与帮助使用原生 popover；未声称对全部弹层焦点做完整审计。

## 3. 代码结构与调用链

```text
ui/
  app.py                # 薄入口；把项目根放到模块搜索路径首位
  workbench.py          # 页面状态、HTTP调用、会话、消息、草稿和工单
  styles.py             # 设计令牌、明暗主题、布局、响应式
  enhancements.js       # 本地DOM字数/中文辅助标签；无网络/存储/自动提交
app/
  main.py               # HTTP输入边界、管理接口、health索引时间
  business.py           # 持久化重命名/删除/编辑草稿与审计事务
  input_safety.py        # 有限凭证识别、文本与递归显示遮蔽
tests/
  test_week2_ui.py       # 19项Streamlit AppTest
  test_frontend_business.py # 19项后端/数据边界测试
.streamlit/config.toml  # 本机监听、默认主题、框架导航与菜单设置
```

页面主链：`main → initialize_state → render_styles → 同步health/会话 → render_sidebar → 读取当前会话 → render_result/render_drafts → render_composer`。发送经`submit_pending → ask_question → POST messages`进入原单Agent；确认仍走固定后端接口和事务，不交给模型。HTTP响应在展示前经过`redact_sensitive_value`；新提问、改名、编辑分别在页面和HTTP/业务边界检查凭证。

关键入口按符号定位：`ui/workbench.py`中的`request_json`、`render_sidebar`、`render_session_menu`、`render_result`、`render_draft_editor`、`render_drafts`、`render_composer`、`main`；`ui/styles.py`的`render_styles`；`ui/enhancements.js`；[输入边界](../app/input_safety.py)；[管理路由](../app/main.py)；[业务事务](../app/business.py)的`rename_session`、`delete_session`、`update_draft`。

### 新增或补充的API契约

| 接口 | 输入与结果 | 约束 |
|---|---|---|
| `PATCH /api/sessions/{id}` | `{title}` → 更新后的session | 标题1–80字；拒绝空白、额外字段与已识别凭证；所有者校验，审计。 |
| `DELETE /api/sessions/{id}` | `{}` → `{status:session_deleted,session_id}` | 处理中返回409；先验所有者并清图checkpoint，再事务删除会话/消息/草稿；已建工单保留，解除draft关联，保留审计映射。 |
| `PATCH /api/drafts/{id}` | `{title,description,category}` → `{status:draft_updated,draft}` | 仅pending可改；标题1–120字、描述1–2000字；category为vpn/email/device，priority仍normal；记录changed_fields。 |
| `GET /api/sessions/{id}` | 消息和当前草稿 | confirmed草稿恢复时带已关联ticket_id，避免刷新后丢失编号。 |
| `GET /api/health` | `index.updated_at`与`updated_at_source` | 时间来自`index_manifest_mtime`；只检查本机状态，不调用远端模型。 |

确认/取消请求仍是`{}`，客户端不能在确认时覆盖持久化草稿字段。删除checkpoint和业务数据库不构成跨库原子事务；若checkpoint清理成功但业务删除失败，业务历史仍是事实来源，页面报错后可重试，不承诺从删除掉的诊断checkpoint恢复。

## 4. 测试与实屏证据

全套 **170项自动化测试通过**，含19项UI与19项前端配套后端测试。测试使用Provider替身及临时数据，不是170条真实模型质量样本。日志、耗时与命令见[验收摘要](evidence/frontend-checks.json)、[完整日志](evidence/frontend-tests.log)、[JUnit](evidence/frontend-tests.xml)、[UI日志](evidence/frontend-ui-tests.log)、[UI JUnit](evidence/frontend-ui-tests.xml)、[Ruff](evidence/frontend-ruff.log)与[依赖检查](evidence/frontend-pip-check.log)。

浏览器核验了首屏、预填不发送、空白禁用、中文提示、实时字数、Shift+Enter、Enter、历史会话恢复、用户右气泡、来源和运行记录、已确认工单编号、知识库刷新、浅暗主题、手机侧栏和无横向溢出。为验证Enter，只在新建演示会话发送了一条明确数据库维修规则题；回复为`no_evidence`，运行记录显示模型/Embedding/工具均0，未产生远端模型调用，未创建工单。此项是工程交互验证，不扩大历史评测样本。

未做新的真实模型问答质量实验、跨机器安装、压测、全量读屏或真实企业集成验收。第三/四周证据摘要另行复核保持不变，不能把本次170项功能测试改写成模型准确率提升。

## 5. 五分钟页面演示

1. 打开工作台，指出本地模拟边界、知识库数量和索引时间；点击一个推荐问题，展示只预填、不自动发送。
2. 打开已有演示会话，查看用户与助手消息、引用原文、只读步骤和运行指标；说明引用ID合法不等于答案全部被支持。
3. 打开一条pending草稿，修改字段并保存，说明此时没有创建工单；确认后看工单号，再刷新确认编号仍存在。现场执行会写本机模拟工单，不联系真实IT。
4. 切到历史工单，查看中文状态；演示会话重命名。删除是持久删除，应只对明确可丢弃的演示会话操作，已建工单会保留。
5. 切换明暗主题并折叠侧栏；说明超时保留请求编号、可刷新恢复，以及有限凭证规则仍不能保护所有敏感资料。

如需演示模型问答，沿用[第四周演示脚本](week4-demo.md)，真实API会产生费用。已有第四周W405超时与质量限制继续保留，不因页面升级抹去。

## 6. 环境与责任边界

`ITDESK_UI_DEBUG=true`显示去凭证origin和调试信息；默认关闭。`ITDESK_UI_MODE=product`只移除“本地演示”badge，仍保留模拟资料和本机存储说明；它不提供认证、RBAC或生产安全能力。两个UI开关从进程环境读取，不自动读`.env`，PowerShell示例见README。

本地固定身份、明文业务库、有限敏感词规则、框架DOM选择器兼容性仍是限制。`enhancements.js`只补原生控件显示与标签，框架升级后应复验选择器和页面布局；核心提交、确认和持久化不依赖该脚本。所有源码为AI辅助开发，简历责任范围应以本人能解释、修改和复现的内容为准。
