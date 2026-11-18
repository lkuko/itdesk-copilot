# 第四周交付审计

审计日期：2026-11-17。依据用户确认的[第四周计划](week4-plan.md)，本周修复第三周已暴露的问题、执行定向回归并完善演示。范围仍是本地单Agent、三个只读工具和模拟工单。原始三周方案没有第四周，本次属于追加迭代；未增加企业登录、多Agent或部署平台。

## 五项任务核对

| 计划任务 | 已交付实现与验证 | 结论与边界 |
|---|---|---|
| 1. 查询保留与工具协议 | [grounded_search_args](../app/guardrails.py)只修正合法搜索参数；本轮用户标识优先，无码且有追问词时才看最近用户消息。[Provider](../app/providers.py)请求单工具，[Agent](../app/agent.py)仍拒绝并行批次且限制3次。[第四周测试](../tests/test_week4_agent.py)强制制造并行拒绝后丢码；W401/W407实际轨迹保留DEV-101并召回device-001。 | 已覆盖已知丢码路径；`requested_args`与实际`args`可对照。不是通用实体识别，用户给错码或未匹配格式仍可能失败。 |
| 2. 草稿事实保护 | [sourced_ticket](../app/guardrails.py)生成类别固定标题与明确归因的用户原话；不将助手历史或模型自由改写落为用户事实。[回归原始结果](evidence/week4-results.jsonl)的W402/W404/W408及[HTTP trace](evidence/week4-http-trace.json)展示保存后的草稿；测试覆盖否定经历、资产/报修码、截断、有限凭证遮蔽与重放。 | D015/A033对应的事实扩写路径已改为确定性原话模板；用户陈述未独立核实，模型仍决定是否提议及类别，有限遮蔽不等于完整脱敏。 |
| 3. 无依据修复SQL边界 | [requires_database_change](../app/guardrails.py)在模型分诊前检查明确维修动作，经`_validate`返回边界说明；W403及HTTP规则请求均为no_evidence、0模型调用。测试同时检查普通SQL概念问题不误入该分支。 | 已覆盖D020请求，不能将规则成绩计成模型成绩。有限词匹配仍有误判/漏判；系统始终没有SQL执行工具。 |
| 4. 评测缺陷分责 | [live_contract](../evaluation/metrics.py)将短语命中改列risk_signals，来源复核判断否定/引用；[更正记录](../evaluation/datasets/corrections-v2.json)公开A009金标和A55覆盖问题。新版A55在`finish_turn`助手回复INSERT处注入失败，检查草稿、回复、审计与完成结果均不残留；[独立确认故障](evidence/week4-confirm-write-fault.json)仍检查确认审计失败整体回滚。[评测测试](../tests/test_evaluation.py)和[故障测试](../tests/test_week3_failures.py)覆盖两个路径。 | 新故障子集10/10、额外确认写失败1/1通过。A009更正题W405本轮发生Provider超时，回答质量尚未得到验证；失败保留。新机械规则通过不能直接称模型质量提升。 |
| 5. 回归、演示与交付 | [12题及金标](../evaluation/datasets/week4.json)单独[冻结](../evaluation/datasets/week4-freeze.json)，[manifest](evidence/week4-manifest.json)、[全部响应](evidence/week4-results.jsonl)、[机器报告](evidence/week4-report.json)、[AI逐条复核](evidence/week4-review.json)和[分析](evidence/week4-analysis.json)分别保存。[当前设计](final-design.md)、[演示脚本](week4-demo.md)、[README](../README.md)、[简历材料](resume-project.md)已更新。 | 本轮完整执行并保留失败，不挑最佳重跑；非盲回归不替代独立验收。逐条质量为AI辅助评分，真人指标仍未验证。 |

## 实际结果与分母

- **12条图请求：11条调用真实Provider，W403由确定性规则返回；另执行1个真实历史轮次。** 共9条业务、3条异常，包括已暴露原题、类似改写和正常/边界对照，不是12条新模型盲测。
- AI辅助任务正确 **11/12**；9条实质回答/草稿证据支持 **9/9**。W405因`provider_timeout`失败，保留原始结果且没有为追求通过而重跑；不能称全部问答质量通过。
- 5条带文档金标的配对检索，两策略Recall@5均为1.000，本轮仍未测出组合策略额外收益。文档命中不代表关键段落或答案完整。
- W412有1次工具超时，后续恢复；分母为17次工具尝试。不能称全程零超时；超时、主轮/历史Token和未知费用详见[实测报告](week4-report.md)。
- 第三周原始证据清单的28个文件摘要保持不变，原冻结题和成绩未倒改，核查结果见[最终检查](evidence/week4-checks.json)。本周模型/规则混合、机械判定修订及小样本均使新旧整体百分比不宜直接比较。

## 自动化、复现与HTTP演示

主环境 **135项测试通过，32.68秒**；Ruff检查`app/ui/tests/evaluation`及`scripts/week2-smoke.py`、`scripts/week4-smoke.py`通过，`pip check`通过。原始命令、退出结果与运行就绪证据见[检查记录](evidence/week4-checks.json)，当前源码见[位置索引](evidence/week4-source-map.json)与[文件摘要](evidence/week4-source-sha256.json)。

[复现记录](evidence/week4-reproduction.json)表明：同一Windows机器的独立源副本刷新源码，复用既有独立venv，实际执行setup、`pip check`和135项测试并全部通过。该副本没有复制真实密钥、索引或运行库，也没有调用付费API；它是既有环境刷新验证，不能称新机器或全新环境验收。

[HTTP演示](evidence/week4-http-trace.json)共**10条HTTP记录**，其中1个新模型草稿请求、1个规则请求，其余用于会话、重放、确认与核查。完成请求重放返回同一回复；同一草稿两次确认返回同一编号，仅新增1张本机模拟工单。10条记录不是10个模型质量样本，不扩大12题回归分母。现场执行入口为[scripts/week4-smoke.py](../scripts/week4-smoke.py)，需先启动API，会产生模型费用及本机模拟记录。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check app ui tests evaluation scripts/week2-smoke.py scripts/week4-smoke.py
.\.venv\Scripts\python.exe -m pip check
# 新回归运行会产生API费用；使用新目录，保留本轮首轮结果
.\.venv\Scripts\python.exe -m evaluation.run --split week4 --output data/evaluation/week4-reproduce
.\.venv\Scripts\python.exe -m evaluation.report --run data/evaluation/week4-reproduce
```

新运行生成自己的`review.json`，必须重新对照该次原文评分；不能套用本轮9/9。完整调用事实与逐题理由以[第四周实测报告](week4-report.md)及其证据为准。当前交付完成的是有边界的修复、验证和失败记录；A009回答重试效果、真人支持率、真实业务收益和跨机器部署均未冒称完成。
