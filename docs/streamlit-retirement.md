# Streamlit 原型退役记录

2026-11-18，React 工作台接替 Streamlit 成为项目的前端入口。退役前已通过浏览器回归和 React 到 FastAPI 的 HTTP 集成验证。[React 验收记录](react-delivery.md)

退出旧界面后，`127.0.0.1:8501` 已释放。React 和 FastAPI 保持各自的监听进程，`5173` 首页与 `8000/api/health` 均返回 HTTP 200。

| 变更 | 核验结果 |
|---|---|
| 归档旧前端 | 归档 `ui/` 中五个源文件、`.streamlit/config.toml`、`scripts/start-ui.ps1` 和两个仅服务旧界面的测试文件。九个文件的原始 SHA-256 与归档一致。 |
| 精简安装 | 移除 Streamlit 依赖声明及 12 条仅由旧界面需要的锁定项。其余 117 条锁定项沿用原版本。 |
| 验证后端 | 从独立 Python 3.11 环境按新锁安装，项目元数据与环境中均无 Streamlit。`pip check`、Ruff 和 209 项 pytest 全部通过。 |
| 核对历史记录 | 两个旧测试文件对应原 React 交付快照中的 58 个旧界面用例。旧报告继续记录当时的 228 项，现行全量套件另含 39 个后续增加的用例。 |

归档操作前后对照了资料库、历史验收文件、配置和可读取的业务文件，共 147 个路径。实际 `.env`、业务库、旧显示偏好、知识资料和既有证据的 SHA-256 一致。对照中唯一差异为同期调整的 `.env.example` 模板。运行中的 Checkpoint 数据库文件未做字节对照。

本机恢复资料在项目的 `work/streamlit-retirement/` 下。`previous/` 包含原路径结构的 11 个文件，包括退役前的 `pyproject.toml` 和 `requirements.lock`。`archived/` 保存九个移出的文件。`manifest.json` 记录 SHA-256，`verification.json`、`listeners.json`、`pytest.log` 和 `pytest.xml` 保存核验结果。该目录由 Git 忽略，恢复依赖保留本机副本。

需要读取历史实现时，可直接查看 `previous/ui/` 和 `previous/tests/`。需要重现旧界面时，可核对 `manifest.json`，把归档的 `ui/`、`.streamlit/config.toml`、启动脚本和旧测试复制到独立的本地副本，并在该副本中使用 `previous/requirements.lock` 安装独立 Python 环境。旧入口仍按当时脚本运行，使用本机测试数据。旧业务和配置文件保留在当前项目，历史归档只包含源码和依赖清单。
