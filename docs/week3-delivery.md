# 第三周交付审计

> 本文记录历史交付。旧源码入口和界面测试请查[原型归档](streamlit-retirement.md)。

审计日期：2026-11-17。以[原开发方案](development-plan.md)“第三周：补测试、实验与简历材料”为逐项清单，范围保持为本地单 Agent、三个只读工具、原创模拟资料和确认后创建模拟工单。本文记录交付证据；质量数字、目标是否达成及失败解释以[第三周实测报告](week3-report.md)为准。

用户已选择“先由 AI 对照原文逐条复核，报告明确标为 AI 辅助评分”。因此原计划中“人工复核用例”和“人工评估答案证据支持率”没有被冒充完成：当前是 AI 辅助编写、来源核对及答案逐条复核，**独立真人评分与原定人工支持率指标尚未验证**。

## 1. 第三周五项计划逐项核对

| 原计划条目 | 本次交付与证据 | 状态及限制 |
|---|---|---|
| 1. 完成模型异常、工具异常、无证据、重复确认和数据库失败测试 | [故障注入实现](../evaluation/faults.py)的`execute_fault`；[第三周故障测试](../tests/test_week3_failures.py)、[Agent 测试](../tests/test_agent.py)、[业务事务测试](../tests/test_business.py)、[HTTP 边界测试](../tests/test_week2_api.py)。逐项观察结果保存在[验收原始结果](evidence/week3-acceptance-results.jsonl)的`fault_result.checks/observed`。 | 主环境最终110条测试通过，故障10/10通过。使用模型替身与临时真实SQLite，工具超时使用真实截止计时，不据此宣称供应商故障率；A55只覆盖实际注入的确认审计回滚，题面错位见第3节。 |
| 2. 构建 80 条用例，20 开发、60 冻结验收，验收含 40 业务、20 异常/安全 | [开发集](../evaluation/datasets/dev.json)、[验收集](../evaluation/datasets/acceptance.json)、[冻结 SHA 与时间](../evaluation/datasets/freeze.json)、[字段和评分纪律](../evaluation/datasets/README.md)；[加载及冻结校验](../evaluation/data.py)。 | 数量与分组已核对，真实运行清单均为 completed。按用户选择采用 AI 辅助复核；不称为独立人工金标或生产请求分布。详见下一节分母。 |
| 3. 对比仅向量与向量加错误码匹配，保留收益和退化 | [检索实现](../app/retrieval/store.py)的`KnowledgeStore.retrieve`；[成对实验](../evaluation/run.py)的`compare_retrieval`；[指标实现](../evaluation/metrics.py)的`retrieval_scores`；[实验边界测试](../tests/test_evaluation.py)。每条知识题的`retrieval_comparison`保留查询、两种排序、片段、召回、MRR与耗时。 | 两方案共享查询向量、索引、Top5和阈值，仅切换完整错误码优先及邻片段策略；未单独证明各机制贡献。验收28条配对题两策略Recall@5均为1.000，收益0、退化0、持平28；本轮没有测出额外召回收益。 |
| 4. 输出分类、检索、答案质量、时延和用量报告 | [报告生成器](../evaluation/report.py)、[开发机器报告](evidence/week3-dev-report.json)、[验收机器报告](evidence/week3-acceptance-report.json)、[可读报告](week3-report.md)。[开发逐条复核](evidence/week3-dev-review.json)、[验收50条合并复核](evidence/week3-acceptance-review.json)保存判定理由。 | 自动契约、引用ID、AI证据支持和任务正确分开报告。验收类别/意图Macro-F1均为0.953；AI辅助实质回答支持37/38、任务正确49/50，均有分母及逐题解释。费用未知保持null，不记为零。 |
| 5. 整理架构图、真实 trace、设计取舍、演示脚本和 README | [最终架构与端到端链路](final-design.md)、[四组技术九问与业务五问](final-design.md)、[五分钟演示脚本](week3-demo.md)、[简历说明](resume-project.md)、[README](../README.md)；完整trace见[验收JSONL](evidence/week3-acceptance-results.jsonl)，[摘录](evidence/week3-traces.json)标记执行模式。 | 材料与运行证据已交付。评测从本地`AgentService.ask`执行，不称为UI或完整HTTP压测；[第二周HTTP确认trace](evidence/week2-http-trace.json)保留原周次。开源参考固定入口及边界见[HelpPilot参考记录](reference-source.md)。 |

## 2. 用例数量、冻结与评分分母

| 范围 | 数量 | 实际模式与用途 |
|---|---:|---|
| 开发 D001–D020 | 20 | 真实模型开发题，用于调试；结果与冻结验收单独保存。 |
| 验收业务 A001–A040 | 40 | 真实模型；覆盖知识、状态、本人/不可见/不存在工单、草稿、澄清及多轮。 |
| 验收异常/安全 A041–A050 | 10 | 真实模型；覆盖库外问题、越界写入/身份/密钥请求、凭证与证书、模拟状态边界等。 |
| 验收确定性故障 A51–A60 | 10 | 模型替身、真实控制流与临时数据库；验证命名工程不变量。ID 保留数据文件中的实际拼写。 |
| 合计 | 80 | **20 开发 + 60 验收；验收为 40 业务 + 20 异常/安全，其中 10 条确定性故障。** |

核对时，[开发manifest](evidence/week3-dev-manifest.json)与[验收manifest](evidence/week3-acceptance-manifest.json)均记录completed，分别对应20行与60行结果。10条故障是验收集的组成部分；其后故障演示重跑只验证CLI与恢复，不新增质量用例，见[实测报告](week3-report.md)。70条真实模型案例均完成AI逐题复核，10条故障按命名工程不变量检查。

分类、意图、时延和答案质量使用真实模型案例，并分别报告开发与验收；验收真实模型主问题分母为 50，不能写成 60 或 80。知识检索只在`expected.doc_ids`非空的可回答题上计算，引用 ID 有效率只以实际引用为分母。故障用例单独按命名不变量通过数报告，不能混入真实模型分类或答案质量。

多轮案例先实际执行`history`，再执行主问题。报告的主轮`response.metrics.total_ms`不等于案例`elapsed_seconds`：后者还包含历史轮次与额外成对检索实验。历史额外调用、Token 与时延可从原始结果回看。该实验没有生产流量、独立用户抽样或外部域泛化验证。

冻结记录包含两个数据文件的SHA。首轮manifest实际记录了模型名、参数、应用/知识文件摘要和runner摘要；后来加固恢复校验时才新增的`evaluator_hashes`及参数中的`chat_endpoint_sha256/embedding_endpoint_sha256/index_manifest_sha256`，**没有倒填到首轮manifest**。首轮连续完成、未混合恢复结果；当前runner会拒绝以旧manifest恢复，新实验须使用新目录。[最终源码摘要](evidence/week3-source-sha256.json)另行核对应用/知识与首轮的一致性，不能冒充首轮已记录新字段。执行后没有调整生产逻辑、提示、阈值或冻结金标；失败保留，后续修复及同集重跑须另存并披露验收集已暴露。

## 3. 失败处理与必须通过的测试对应表

下面列出实现与自动化检查的对应关系。主环境最终110条测试通过，见[检查汇总](evidence/week3-checks.json)；同机独立副本最终也通过110条，复现阶段与范围见第5节。

| 要验证的行为 | 实现入口 | 主要检查入口 |
|---|---|---|
| 正常检索、完整错误码、改写、相邻证据和旧索引拒绝 | [ingestion.py](../app/retrieval/ingestion.py)、[store.py](../app/retrieval/store.py) | [test_ingestion.py](../tests/test_ingestion.py)、[test_store.py](../tests/test_store.py)、[test_evaluation.py](../tests/test_evaluation.py)；冻结知识题与成对检索结果。 |
| 多轮补充、历史恢复、同会话并发和请求重放 | [agent.py](../app/agent.py)的`_prepare/ask`；[business.py](../app/business.py) | [test_agent.py](../tests/test_agent.py)的`test_reopened_async_checkpoint_and_business_store_keep_multiturn_history`、请求冲突/重放测试；`tests/test_week2_ui.py`的会话切换与刷新恢复。 |
| 服务正常/故障、工具错误与不可用 | [tools.py](../app/tools.py)的`SERVICE_FIXTURES/ToolExecutor.execute` | [test_agent.py](../tests/test_agent.py)的模拟年龄披露、错误返回与串行恢复；验收服务状态案例；`tool_timeout/index_unavailable`故障。 |
| 工单存在、不存在与不属于本人 | [business.py](../app/business.py)的`get_ticket`；[tools.py](../app/tools.py) | [test_business.py](../tests/test_business.py)、[test_week2_api.py](../tests/test_week2_api.py)的外人/不存在记录同样不可见；`foreign_ticket`故障。 |
| 无证据、非法 JSON、虚构引用，修复最多一次 | [providers.py](../app/providers.py)；[agent.py](../app/agent.py)的`_triage/_final/_validate` | [test_agent.py](../tests/test_agent.py)的非法分诊、最终输出、引用修复及真实本轮证据检查；[test_api.py](../tests/test_api.py)的无证据拒答；`model_bad_json`故障。 |
| 未知工具、错误参数、并行批次与三次额度 | [tools.py](../app/tools.py)；[agent.py](../app/agent.py)的`_plan/_tools` | [test_agent.py](../tests/test_agent.py)的`test_unapproved_tools_and_arguments_execute_nothing`、预算和批次拒绝测试。 |
| 模型超时/认证异常、工具真实截止、Chroma 未就绪 | [providers.py](../app/providers.py)的脱敏异常映射；[tools.py](../app/tools.py) | [test_week3_failures.py](../tests/test_week3_failures.py)的真实适配器异常映射；`model_timeout/tool_timeout/index_unavailable`故障。 |
| 未确认不创建、取消后不创建、重复确认幂等 | [business.py](../app/business.py)的`finish_turn/confirm_draft/cancel_draft`；[main.py](../app/main.py)的固定接口 | [test_business.py](../tests/test_business.py)、[test_week2_api.py](../tests/test_week2_api.py)、`tests/test_week2_ui.py`；`unconfirmed_write/cancelled_confirm/duplicate_confirmation`故障。 |
| SQLite 写入失败整体回滚，不显示成功 | [business.py](../app/business.py)的事务；[main.py](../app/main.py)的存储错误响应 | [test_business.py](../tests/test_business.py)的审计失败回滚、回复/草稿原子保存和并发确认；[test_week2_api.py](../tests/test_week2_api.py)的存储失败；`sqlite_write_failure`故障。 |
| 重启后保存记录与确认可用、Checkpoint 失败不重复建草稿 | [business.py](../app/business.py)；[agent.py](../app/agent.py)；[main.py](../app/main.py)的`lifespan` | [第二周重启证据](evidence/week2-restart.json)；[test_business.py](../tests/test_business.py)的重开存储；[test_agent.py](../tests/test_agent.py)的 Checkpoint 提交失败测试。只承诺恢复已保存业务状态。 |
| 用户/文档诱导绕确认、伪造身份或写工具 | [tools.py](../app/tools.py)的白名单/参数；[schemas.py](../app/schemas.py)的额外字段拒绝；[main.py](../app/main.py)的固定用户 | 真实模型安全题 A041–A050；[test_agent.py](../tests/test_agent.py)的权限字段拒绝；`prompt_injection_document`故障。脚本强制模型尝试恶意工具只证明后端隔离，不代表真实模型免疫注入。 |
| 故障评测不能把未执行/未知故障当通过 | [faults.py](../evaluation/faults.py)的`execute_fault` | [test_week3_failures.py](../tests/test_week3_failures.py)的未知故障拒绝、故意破坏确认机制应判失败、异常脱敏测试；检查实际数据库行与审计记录。 |

人工兜底指用户联系正式 IT，由人核实和处理；此项目没有自动转接、真实工单派送或人工值班平台。不能把“建议联系人工”写成“已转人工”。

**A55覆盖错位：**冻结题面指向`finish_turn`保存失败，实际故障runner在确认时通过`ticket_confirmed`审计触发器注入失败，因此该项只证明确认事务回滚。`finish_turn`回复/草稿/审计回滚由[业务测试](../tests/test_business.py)的`test_answer_commit_failure_rolls_back_draft_and_audit_together`另行验证；确认审计失败由`test_confirmation_rolls_back_ticket_and_draft_if_audit_write_fails`验证。两路径已有自动化检查，但不能说A55直接覆盖题面路径；冻结题保留，下版须明确拆分。

## 4. 报告口径与已知问题

质量报告需同时展示状态/工具契约、类别和意图 Macro-F1、成对检索及 Agent 实际证据、引用 ID、拒答、AI 辅助证据支持和任务正确率。流畅回答、引用存在和图流程结束都不能单独证明业务正确。

逐条复核保留开发D008可答却拒答、D015把未明确确认的账号格式与重试经历写入草稿、D020对越界SQL请求只追问而未明确边界，以及验收A033把故障码误写为设备编号。不能因没有未确认工单就判全部任务成功。[实测报告](week3-report.md)还保留A039否定句关键词误报及A009金标措辞超出原文的问题；首轮机械检查49/50、故障10/10不能改称全部答案正确。

| 原目标/报告项 | 当前证据与结论入口 |
|---|---|
| 分类Macro-F1 ≥0.85 | 验收50条真实模型主问题为**0.953，达到目标**；仅40条业务题为0.931。类别误判保留在报告中。 |
| 可回答Recall@5 ≥0.85 | 验收28条带来源金标题两策略均为**1.000，达到目标**；这是文档级命中，不能证明关键段落完整或答案正确，也没有测出组合策略额外收益。 |
| 引用ID有效率100% | 验收**68/68，达到目标**；只说明引用属于本轮证据，不证明每条语义得到支持。 |
| 人工答案证据支持率 ≥85% | **尚未验证。** 用户选择的AI辅助复核已完成：验收实质回答/草稿支持**37/38（97.37%）**，全部任务正确**49/50（98.00%）**，观察到不安全断言0/50；不能据此宣布原人评目标达标或生产安全。 |
| 未确认创建0次、重复确认新增0次 | **在已测范围内达到**：70条真实模型主问题及另3个历史轮次确认前均无新单；重复确认故障检查只新增1张工单和1条确认审计，返回同一编号。测试另覆盖并发确认、取消和回滚；单机结果不能外推生产零事故。 |
| P50/P95、超时、Token/请求和费用 | 验收50主轮**P50 16.60秒、P95 29.01秒**；无整轮/上游超时终止，但实际工具有**3/60次超时**并后续恢复。主轮平均6,016.4输入、1,371.86输出Token；历史/额外检索单列。费用未知为null，不能写零费用或全程零超时。 |
| 效率、解决率、采纳率或业务收益 | 没有真实企业数据，未验证。不写节省工时、上线规模、真实处理时长下降或生产 SLA。 |

## 5. 复现与演示验收

[Windows启动说明](../README.md)包含独立环境、锁定依赖、空配置模板、真实模型能力检查、建立索引及两个本地进程。[独立环境复现记录](evidence/week3-reproduction.json)区分两个阶段：先在同一Windows机器创建干净源码副本与独立Python 3.11虚拟环境，初次安装检查通过108条测试；最终仅刷新选定源码、保留原独立虚拟环境，再运行setup、`pip check`与测试，**110条通过，30.30秒**，并核对副本源码一致。中间109条运行因并发源码变化没有发布为最终结果。各阶段未复制真实密钥、索引或运行库，没有付费API调用。包缓存和本机环境仍共享，不称新机器或全新缓存验收；真实Provider能力由原配置连续实测验证。

最终主环境验证见[检查记录](evidence/week3-checks.json)：**110条pytest通过，33.78秒**；Ruff检查`app/ui/tests/evaluation/scripts/week2-smoke.py`通过；`pip check`与锁定依赖安装`--dry-run`通过。源码位置及摘要见[源码映射](evidence/week3-source-map.json)和[SHA-256](evidence/week3-source-sha256.json)。

以下命令用于复现对应检查，实际执行结果以证据文件为准：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app ui tests evaluation scripts/week2-smoke.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip install --dry-run -r requirements.lock
```

真实评测会产生模型费用。新运行使用新目录，避免覆盖已冻结的证据；相同身份且仅恢复未完成案例时使用`--resume`：

```powershell
.\.venv\Scripts\python.exe -m evaluation.run --split dev --output data/evaluation/dev-reproduce
.\.venv\Scripts\python.exe -m evaluation.run --split acceptance --output data/evaluation/acceptance-reproduce
.\.venv\Scripts\python.exe -m evaluation.report --run data/evaluation/acceptance-reproduce
# 无需真实模型调用的10条故障检查，同样使用新目录
.\.venv\Scripts\python.exe -m evaluation.run --split acceptance --faults-only --output data/evaluation/faults-reproduce
```

新运行生成自己的待评分`review.json`；不能复制旧答案的评分冒充新运行评审。报告校验结果 SHA 和完整用例 ID，复核应绑定对应原始结果。

周末演示按[五分钟脚本](week3-demo.md)展示知识问答与原文、模拟状态、澄清/拒答、待确认草稿、确认/取消、重复确认、历史恢复和受控失败。真实模型波动时保留实际结果，用 trace 解释失败；故障替身用于稳定展示工程边界。

## 6. 最终交付物索引

| 原方案最终交付项 | 当前入口 |
|---|---|
| 可运行源码、锁定依赖、配置模板、种子数据和 Windows 说明 | [app](../app/)、`ui/`、[requirements.lock](../requirements.lock)、[.env.example](../.env.example)、[模拟知识](../knowledge/)、[业务种子初始化](../app/business.py)、[README](../README.md)。真实凭证只保存在本地`.env`。 |
| 与实际代码一致的架构图 | [final-design.md](final-design.md)整体架构及模块职责表。 |
| 知识问答完整trace与确认执行分支 | [验收完整结果](evidence/week3-acceptance-results.jsonl)的`response.tool_trace/retrieved/metrics`；[第三周trace摘录](evidence/week3-traces.json)；[第二周HTTP确认trace](evidence/week2-http-trace.json)。[第三周报告](week3-report.md)标明输入/输出/耗时和执行模式。 |
| 四项技术九问及源码位置 | [final-design.md](final-design.md)的有状态编排、向量与错误码、结构与引用、确认与事务幂等；[最终源码映射](evidence/week3-source-map.json)与[摘要](evidence/week3-source-sha256.json)。首轮manifest字段边界已在第2节披露。 |
| 业务五问与本人/协作/基座边界 | [final-design.md](final-design.md)业务五问与[resume-project.md](resume-project.md)。明确 AI 辅助实现、框架已有能力、原创模拟数据及没有真实企业上线。 |
| 冻结集、实测报告、失败案例和约五分钟演示 | [数据与冻结规则](../evaluation/datasets/README.md)、[week3-report.md](week3-report.md)、[开发复核](evidence/week3-dev-review.json)、[验收复核](evidence/week3-acceptance-review.json)、[week3-demo.md](week3-demo.md)。节点轨迹见[开发日志](evidence/week3-dev-nodes.log)与[验收日志](evidence/week3-acceptance-nodes.log)。 |

满足以上范围即可作为 v1 收尾。尚未验证的真人指标、真实企业收益和跨机器部署不通过继续添加平台功能来掩盖。
