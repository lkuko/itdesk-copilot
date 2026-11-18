# 知识库实验与机器人协议验收

工作台按 Dify 的导入、分段配置、检索设置和召回测试流程组织，沿用 ITDesk 的 React 组件和视觉风格。首次发布从内置文档创建版本。更换 Embedding、切片参数或文档后构建新索引，成功后切换。产生当前版本后，只改排序参数可以复用索引。[Dify 文档](https://docs.dify.ai/en/cloud/use-dify/knowledge/create-knowledge/setting-indexing-methods) · [工作台实现](../app/retrieval/workspace.py)

本轮在临时知识库中，用真实 `Qwen/Qwen3-Embedding-0.6B` 导入一篇模拟 VPN 手册，再用真实 `Qwen/Qwen3-Reranker-8B` 重排。召回接口返回 `applied`，新手册排第一。221 项 Python 测试、48 项前端单测、25 项浏览器测试和 3 项隔离 HTTP 集成测试通过。[真实 HTTP 集成脚本](../frontend/tests/integration/real-http.spec.ts)

以下记录最初的只读切片实验和 Telegram 本地协议验收。

知识库实验室用于查看片段为什么命中，以及命中后还缺哪些步骤。页面提供手册目录、索引状态、原文和切片位置。语料使用本机 Markdown/TXT。维护者修改源文件后用 CLI 显式重建问答索引。[知识库页面](../frontend/src/features/knowledge/KnowledgePage.tsx) · [切片实现](../app/retrieval/ingestion.py)

用户在知识库页输入查询和 Top-K。FastAPI 读取同一份语料，分别用自然边界和固定窗口切片。两组用相同窗口和重叠，将标题与片段合并为 Embedding 输入。相同文本只计算一次，两组共用查询向量。后端用精确余弦排序，页面显示分数、来源、版本、位置和原文。实验结果不进入实际问答排名，问答仍走 Chroma、错误码和邻片段逻辑。[实验后端](../app/retrieval/lab.py) · [问答检索](../app/retrieval/store.py)

## 切片策略实测

本次使用已有开发题中需要知识检索、无历史上下文且有标注文档的全部 10 道题。语料为 24 篇模拟手册，模型为 Qwen/Qwen3-Embedding-8B，4096 维，窗口 500 字符、重叠 80 字符。两组都做精确余弦排序。Recall@5 以每道题的黄金文档集合为分母再求平均，MRR@5 以第一个黄金文档片段的排名计算。标注属于已知开发题。[逐题原始结果](evidence/chunk-experiment-result.json)

| 切分 | 文档 Recall@5 | MRR@5 | 片段数 | 输入字符数 |
|---|---:|---:|---:|---:|
| 优先自然边界 | 1.00 | 1.00 | 48 | 21,559 |
| 固定字符窗口 | 1.00 | 1.00 | 48 | 21,559 |

两组的文档指标持平。按 AI 逐条对照原文的复核口径，首个片段覆盖的关键事实是自然边界 13/30、固定窗口 20/30。查看两组各自 Top 5 中来自黄金文档的全部片段，均覆盖 30/30。D003 的自然边界组第一片从重新连接开始，时钟和网关步骤由后续片段补齐。固定窗口的首片同时包含这些步骤。当前问答流程使用多个候选片段，保留现有切法。进一步测试时可标明哪些前置条件和验证句必须同时命中。[逐题证据复核](evidence/chunk-evidence-review.md)

首次向量准备返回 `provider_timeout`，当时整批上限为 75 秒。随后离线向量准备放宽到 180 秒，实际花约 49.6 秒，10 道查询全部完成。去重后 94 个文档向量输入共用 25,637 Token。本次只请求 Embedding，费用取决于实际单价。[实验运行器](../evaluation/chunk_experiment.py)

```powershell
.\.venv\Scripts\python.exe -m evaluation.chunk_experiment work/chunk-experiment-new.json
```

运行会调用已配置 Embedding 并可能产生费用，使用新的输出路径。浏览器临时对比每次重新准备两组向量，整轮 Embedding 上限 75 秒，超时后可以手动重试。离线脚本在一轮多题评测中复用文档向量。

## Telegram 本地协议

机器人复用 `AnswerService.ask` 做有引用的文档问答，并记录现有问答诊断信息。配置 webhook secret 和单个允许私聊的 ID 才注册入口，检查发送者、请求规模和近期重复 update。8 秒内无法回答就提示用户稍后重问或联系 IT。生成单独的随机 webhook secret 并放在本地 `.env`。本轮使用合成私聊 update 验证到生成 Telegram `sendMessage` 响应，实际投递待平台环境验证。[机器人源码](../app/bot.py) · [本地机器人用例](../tests/test_bot.py) · [官方 webhook 回复规范](https://core.telegram.org/bots/api#making-requests-when-getting-updates)

## 开源参考与项目负责范围

CyberStrikeAI 固定提交 `1dce196700132d2e24ad9a7ca9690c952d49a4de` 的资料库界面提供列表、预览、索引进度及检索结果。搜到的片段包含相似度，向量字段不序列化。ITDesk 借鉴片段和相似度的展示方式，并用同语料和参数增加切片对照。参考提交采用 Apache-2.0，本轮自行实现，未复制上游代码。[知识库界面](https://github.com/AIPentest/CyberStrikeAI/blob/1dce196700132d2e24ad9a7ca9690c952d49a4de/web/static/js/knowledge.js) · [检索类型](https://github.com/AIPentest/CyberStrikeAI/blob/1dce196700132d2e24ad9a7ca9690c952d49a4de/internal/knowledge/types.go#L69) · [许可证](https://github.com/AIPentest/CyberStrikeAI/blob/1dce196700132d2e24ad9a7ca9690c952d49a4de/LICENSE)

上游机器人模块把渠道消息适配与应用处理拆开，Telegram 使用轮询。ITDesk 只接收单个私聊的 webhook，并调用已有的文档问答。简历应围绕本人能独立复现的页面、接口、实验解读和业务流程陈述。代码与评测有 AI 协作，消息协议、模型、框架和数据库能力来自各自基座。[上游渠道文档](https://github.com/AIPentest/CyberStrikeAI/blob/1dce196700132d2e24ad9a7ca9690c952d49a4de/docs/zh-CN/robot.md#L33) · [面试准备](resume-project.md)

## 可重复验收

本轮通过 210 项 pytest、48 项 Vitest、23 项 Playwright 浏览器测试及 2 项独立 HTTP 集成。前端构建、ESLint 和相关 Python Ruff 检查通过。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run lint
npm --prefix frontend run build
npm --prefix frontend run test
Set-Location frontend
npm run test:e2e
npm run test:e2e -- --config playwright.integration.config.ts
```

独立集成使用临时真实 FastAPI、SQLite 和 Chroma，并替换模型为确定性测试实现。覆盖会话、草稿确认、对照请求和活跃索引状态。对照页面另有窄屏、过滤、原文预览与异常用量显示的浏览器测试。真实 Embedding 的对照实验单独记录。[HTTP 集成](../frontend/tests/integration/real-http.spec.ts) · [知识库浏览器用例](../frontend/tests/e2e/knowledge.spec.ts)
