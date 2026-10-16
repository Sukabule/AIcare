# 项目导航

整理日期：2026-10-15。当前使用说明以 [项目 README](../README.md) 和源码为准；归档中的测试数量、库存、接口状态只代表当时记录。

当前仓库整理说明见 GitHub 整理记录（本地文件，不随仓库提交）。

## 文档入口

| 目的 | 文档 |
|---|---|
| 启动、配置、常用命令、功能边界 | [项目 README](../README.md) |
| 理解完整架构、数据关系、请求流程 | [项目深度阅读报告](project-architecture-reading.md) |
| 查看当前页面设计和对齐修复 | 前端设计记录（本地文件，不随仓库提交） |
| 排查 Milvus / etcd 启动 | [Milvus 恢复记录](milvus-startup-recovery.md) |
| 查历史需求、阶段验收和截图 | 历史归档索引（本地文件，不随仓库提交） |
| 查看结构与性能重构 | 重构记录（本地文件，不随仓库提交） |
| 查看本次清理范围和验证 | 整理记录（本地文件，不随仓库提交） |

## 目录职责

| 路径 | 内容与边界 |
|---|---|
| [app/main.py](../app/main.py)、[app/config.py](../app/config.py) | 应用装配、页面与 API 注册、配置入口 |
| [app/api/](../app/api/) | HTTP 输入输出、SSE、后台管理接口 |
| [app/graph/](../app/graph/) | 当前聊天主流程：分流、状态、暂停恢复、运行时 |
| [app/core/](../app/core/) | 模型适配、提示词、RAG、上下文预算、评估、观测、后台作业 |
| [app/tools/](../app/tools/) | 内置工具、MCP 发现与工具执行治理 |
| [app/kb/](../app/kb/) | 材料读取、切块、去重、挖掘、双写与审核写回 |
| [app/db/](../app/db/) | ORM 映射和异步仓储；实际迁移由 SQL 管理 |
| [app/schemas/](../app/schemas/) | 聊天、操作和结构化提取契约 |
| [app/static/](../app/static/) | 六个页面及公共样式/脚本，无独立前端构建工程 |
| [mcp_servers/](../mcp_servers/) | 独立运行的物流和售后演示服务 |
| [scripts/](../scripts/)、[Makefile](../Makefile) | 启动、运维、知识建设、评估与冒烟入口 |
| [sql/](../sql/) | Ch02–Ch09 建表、迁移、种子数据与独立测试库初始化 |
| [config/](../config/) | etcd 容器配置 |
| [tests/](../tests/) | 回归测试、mock 和标注样例；数据库测试会清理专用测试库 |
| [data/](../data/) | 知识材料、评估产物、会话检查点、历史语料，不作缓存清理 |
| [docs/](./) | 当前文档入口、修复说明及历史归档 |
| `log/` | 运行日志、后台作业日志与验收证据 |
| `.cache/` | tokenizer、工具、评估断点等本地状态，不能整体删除 |
| `.venv/` | uv 管理的 Python 环境，本次保留 |

## 核心调用链与阅读顺序

1. [main.py](../app/main.py)：配置、数据库、知识服务、模型与 GraphRuntime 怎样装配。
2. [chat.py](../app/api/chat.py) / [agent.py](../app/api/agent.py) → [runtime.py](../app/graph/runtime.py)：流式与非流式请求如何共用状态与持久化。
3. [build.py](../app/graph/build.py) → [routing.py](../app/graph/routing.py) → [nodes.py](../app/graph/nodes.py)：指代补全、九类意图、知识/业务/售后路径。
4. [rag.py](../app/core/rag.py) → [retrieval.py](../app/core/retrieval.py) → [milvus_client.py](../app/kb/milvus_client.py)：检索、融合重排、证据检查与拒答。
5. [registry.py](../app/tools/registry.py) → [engine.py](../app/tools/engine.py) / [mcp_client.py](../app/tools/mcp_client.py)：发现、校验、执行、重试与审计；对照 [actions.py](../app/api/actions.py) 的用户表单路径。
6. [budget.py](../app/core/budget.py) → [memory.py](../app/core/memory.py) → [summarizer.py](../app/core/summarizer.py)：模型窗口预算和三层上下文。
7. [flywheel.py](../app/core/flywheel.py) → [review_repository.py](../app/db/review_repository.py) → [review_writeback.py](../app/kb/review_writeback.py)：原话归并、人工审核与知识补全。

旧 `app/core/agent.py` 的 AgentService 和 SessionMemory 属于早期实现，当前聊天入口采用 GraphRuntime。本次只整理文档和缓存，未合并或删除这些实现。

## 脚本入口速查

| 任务 | 入口 |
|---|---|
| Windows / Bash 一键开发启动 | [dev.ps1](../scripts/dev.ps1)、[dev.sh](../scripts/dev.sh) |
| 数据库迁移 | [init_db.py](../scripts/init_db.py)；`--test` 同时准备测试库 |
| MCP 进程管理与无付费模型冒烟 | [mcp_services.py](../scripts/mcp_services.py) |
| 材料预览、建库、向量化、会话挖掘 | [kb_cli.py](../scripts/kb_cli.py) |
| 知识飞轮 | [flywheel_pipeline.py](../scripts/flywheel_pipeline.py) |
| RAG 评估 / 小样本飞轮评估 | [eval_ch04.py](../scripts/eval_ch04.py)、[eval_flywheel.py](../scripts/eval_flywheel.py) |
| 意图、指代、扩写评估 | [eval_intent.py](../scripts/eval_intent.py)、[eval_coref.py](../scripts/eval_coref.py)、[eval_expand.py](../scripts/eval_expand.py) |
| token 报告 / 阈值校准 | [cost_by_intent.py](../scripts/cost_by_intent.py)、[calibrate_confidence.py](../scripts/calibrate_confidence.py) |
| 可选 Langfuse 初始化 | [init_langfuse.py](../scripts/init_langfuse.py) |

知识挖掘、飞轮、向量化和真实评估可能调用模型/嵌入/重排服务；本次文档整理没有运行这些任务。

## 报告与归档规则

- `data/ch04/` 至 `data/ch09/`：现有评估和观测产物，路径被运行时代码使用，保留原位。
- `data/checkpoints/`：持久会话状态，不可作为临时文件清理。
- `data/ch10/`：已撤销分类器的历史语料，保留但不接入当前应用。
- `data/ch03/reports/retrieval_eval.json`：`kb_cli.py eval-retrieval` 的现有输出路径。
- `data/ch03/reports/mining_eval.json`：`eval_mining.py` 的现有输出路径。
- archive/chapters/（本地文件，不随仓库提交）：各章节笔记、计划、截图及评估快照。同目录保留原文件名，笔记中的同名附件仍可定位。

历史笔记中反引号标出的 `app/`、`scripts/`、`data/`、`sql/`、`log/`、`.cache/` 路径均以项目根目录为基准；Markdown 相对链接以文档所在目录为基准。旧命令及已撤销功能仅供追溯，不视为当前操作指南。
