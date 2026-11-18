# React 前后端分离：交付与复现

本文是 React 迁移验收当时的快照。后续知识库和机器人入口的验收见[本轮交付](knowledge-lab.md)，旧界面处理见[退役与恢复](streamlit-retirement.md)。

本轮把 React + TypeScript + Vite 作为主界面，FastAPI 继续承载 Agent、检索和工单事务。目标是一个能独立复现、能解释失败处理的中等难度简历项目。旧 Streamlit 保留调试入口，旧评测和历史证据保留原样。

开发安排见 [四阶段计划](development-plan.md)，整体架构、端到端链路、五组技术九问与业务五问见 [设计研究](react-design.md)，字段、错误码和 CORS 见 [接口契约](react-api-contract.md)，简历表述见 [面试材料](resume-project.md)。

## 交付范围与源码

| 范围 | 结果与入口 |
|---|---|
| 独立前端工程 | [`frontend/package.json`](../frontend/package.json)、锁文件、React 路由和响应式普通 CSS。主界面为聊天、历史工单、设置。 |
| REST 适配 | [`api/client.ts`](../frontend/src/api/client.ts)、[`types.ts`](../frontend/src/api/types.ts)、[`errors.ts`](../frontend/src/api/errors.ts)。GET 默认 5 秒，写请求默认 100 秒；不自动重发写请求，区分 HTTP 错误和 HTTP 200 的业务失败。类型声明不等于完整运行时 Schema 校验。 |
| 会话与聊天 | [`App.tsx`](../frontend/src/app/App.tsx)、[`ChatView.tsx`](../frontend/src/features/chat/ChatView.tsx)。新建、分页、切换、重命名、删除、推荐预填、引用原文、只读工具及可选用量。Markdown 不渲染原始 HTML。 |
| 请求恢复 | [`pending.ts`](../frontend/src/features/chat/pending.ts) 保存当前标签页的原问题和请求编号。刷新先核对已保存结果；同编号完成结果由后端重放；中断/编号冲突后，用户可明确选择新编号。放弃前端重试不会取消后端。 |
| 草稿与工单 | [`DraftList.tsx`](../frontend/src/features/drafts/DraftList.tsx)、[`TicketsPage.tsx`](../frontend/src/features/tickets/TicketsPage.tsx)。编辑与保存不建单，确认只传草稿编号和 `{}`。保存/确认/取消丢响应后先刷新服务器实际状态与内容。 |
| 八项显示偏好 | [`preferences.ts`](../frontend/src/features/settings/preferences.ts)、[`SettingsPage.tsx`](../frontend/src/features/settings/SettingsPage.tsx)。主题、字号、密度、示例、时间、指标、来源展开、工具展开；版本化 localStorage，保存成功后应用。 |
| 后端跨域 | [`config.py`](../app/config.py)、[`main.py`](../app/main.py)。默认仅允许两个本机 5173 Origin，支持 JSON 数组配置；CORS 不是认证。 |

浏览器只保存显示偏好和本标签页的未决请求；真实模型地址、密钥仍留在 Python 后端。前端有限凭证检测和遮蔽只提供辅助，后端负责输入边界。localStorage 与 Streamlit 的 `data/ui-preferences.json` 不同步，不自动迁移。

## 启动与演示

首次按 [README](../README.md) 配置 Python 3.11 环境、后端 `.env` 和知识索引；使用 Node.js 22.12+ 安装前端锁定依赖：

```powershell
npm --prefix frontend ci
```

分别在两个项目根目录终端运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-api.ps1
```

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-frontend.ps1
```

打开 `http://127.0.0.1:5173`。默认 API 为 `http://127.0.0.1:8000`；如需修改，将 `frontend/.env.example` 复制为前端自己的 `.env` 并设置 `VITE_API_BASE_URL`，然后重启 Vite。不要把模型密钥放进任何 `VITE_*` 配置。

建议按以下顺序演示，真实模型提问会产生供应商费用：

1. 点击“VPN 连接失败”，说明只预填；修改后发送，展开手册原文和只读工具记录。
2. 询问“现在 VPN 服务正常吗？”，说明读的是固定模拟快照。
3. 请求准备人工核查草稿，编辑后保存，指出仍未创建；核对后按钮确认，刷新后在历史工单找到同一编号。
4. 用刚创建的编号查询工单，再新建另一份草稿并取消，展示人工确认边界。
5. 修改主题或显示设置，保存、刷新；最后展示一个无证据问题和一条失败/超时回归证据。

本轮修复了实际交互与可访问性问题：草稿 PATCH 已提交但响应丢失时，禁止按旧显示内容确认；分页旧响应不能覆盖新页；原会话后台请求完成时，不能使浏览器返回后的当前会话历史一直空白；设置标签支持方向键、Home/End 和焦点移动。

## 验证方式与边界

验证分成三种，不能合并为“真实模型全部通过”：

- Python 回归验证后端、原 Streamlit 与 CORS；Vitest 验证 API 错误、未决请求、设置及有限凭证检测。
- 默认 Playwright 验证接口替身驱动的页面流程，其中独立 health smoke 只读取本机真实 API。
- 独立真实 HTTP 集成使用 [`react_smoke_api.py`](../scripts/react_smoke_api.py)：真实 FastAPI → Agent 图 → 三个只读工具 → SQLite/Chroma，Provider 为确定性脚本输出，数据目录为一次性临时目录。不读取正常 `.env` 的配置，不访问远端模型，也不向正常业务库建单。它证明工程协作，不衡量模型能力。

常规检查从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app ui tests evaluation scripts
.\.venv\Scripts\python.exe -m pip check
npm --prefix frontend run build
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run test:e2e
```

首次安装测试浏览器，然后在独立前端目录执行真实 HTTP 集成：

```powershell
cd frontend
npx playwright install chromium
npx playwright test --config playwright.integration.config.ts
```

集成配置使用 8010 后端与 5174 Vite；需要这些端口可用。它可以复用已经运行的确定性测试 API，不应把其他服务放在该端口。主演示仍为 8000/5173。

最终执行结果、截图及源码摘要见本页后续验证记录和 `docs/evidence/react-*`。第三/四周模型结果保持历史日期和分母，不能据本轮页面迁移宣称准确率、实际员工效率或成本改善。

## 简历与责任

重点讲清四项：HTTP 分层和错误状态、受限只读 Agent、来源可核查的检索、请求恢复与确认事务。设置和视觉属于展示完整性，不必硬包装成 Agent 核心技术。

当前为固定演示身份、本机单 worker、模拟资料和工单；没有登录、多用户授权、真实企业接入、Docker、SSE 或云部署。本人能独立复现和修改的部分、AI 辅助实现的部分、框架已有能力应分别说明。完整的九问/五问与候选人责任边界见上述设计研究和简历文档。

## 2026-11-18 执行记录

| 检查 | 本轮结果 | 证据 |
|---|---|---|
| Python 全量回归 | 228 通过，包含原后端/Streamlit/CORS | [pytest 日志](evidence/react-python-tests.log)、[JUnit](evidence/react-python-tests.xml) |
| Python 静态与依赖 | Ruff 通过；pip check 无依赖冲突 | [Ruff](evidence/react-ruff.log)、[依赖检查](evidence/react-pip-check.log) |
| React 生产构建 | TypeScript 与 Vite 构建通过 | [build](evidence/react-build.log) |
| ESLint | 0 错误、0 警告 | [lint](evidence/react-lint.log) |
| Vitest | 5 文件、48 项通过 | [单测](evidence/react-vitest.log) |
| Playwright mock E2E | 21 项通过，4 个 worker；含会话/草稿/聊天/设置/手机浏览器流程 | [浏览器回归](evidence/react-mock-e2e.log) |
| 真实 HTTP 集成 | 1 项通过：配置从零启动临时 API/Vite，验证真实 React→FastAPI→Agent→只读工具→SQLite/Chroma；Provider 确定性，无模型调用，结束后测试端口自动关闭 | [集成报告](evidence/react-real-http-integration.log)、[JSON](evidence/react-real-http-integration.json) |
| 桌面与手机实屏 | 1280×720、390×844 检查；手机首页/设置无横向溢出；推荐只预填、Shift+Enter 换行、深色保存刷新保留、设置方向键选中与焦点、手机导航可用 | 下方截图 |

浏览器实屏使用正常 8000 后端，仅读取健康/历史，问题未发送给真实模型；设置保存后已恢复默认。视口临时覆盖已恢复。历史 97 个证据文件的内容哈希、正常业务库各表内容、真实 `.env` 及旧 Streamlit 显示配置在验收前后对照；源码清单含 72 个前后端源文件、测试与启动文件，详见[保护核验](evidence/react-protection-check.json)、[源码 SHA-256](evidence/react-source-sha256.json)和[Markdown 链接核验](evidence/react-doc-links.json)。保护基线在本轮前端实现期间、浏览器集成验收之前建立，只证明该验收区间的未变化。

依赖审计 `npm audit --omit=dev` 为 0；完整审计还有 **11 个 high 依赖节点**，均由开发工具链的同一个 picomatch ReDoS 公告传播，不是 11 项独立漏洞。已安装及当日 registry 最新 picomatch 均为 4.0.7，暂无正常补丁升级；未用不兼容降级或 `--force` 隐去结果。影响开发/构建/测试/lint 的模式匹配，不能声称开发依赖没有风险。详见 [原始审计](evidence/react-npm-audit.json)。

在独立忽略目录复制 package/lock 后，仅把 Vitest 改为 npm 建议的 3.1.4 并重新计算锁文件，审计变为 2 critical、10 high、1 moderate；因此未采用这一降级。该试算不改变主工程或表示旧版本测试兼容性已通过，详见[依赖决策](evidence/react-dependency-decision.json)与[降级审计](evidence/react-vitest3-downgrade-audit.json)。

![React 桌面首页](evidence/react-desktop-home.jpg)

![深色设置保存并刷新](evidence/react-desktop-settings-dark.jpg)

![390px 手机首页](evidence/react-mobile-home.jpg)

![390px 手机设置及键盘焦点](evidence/react-mobile-settings.jpg)
