# 贡献指南

## 开发环境

使用 Python 3.12 和 uv，在项目根目录运行：

```bash
uv sync --locked
uv run python scripts/check.py
```

这组检查包括 Ruff 静态检查、格式检查及选定的独立回归测试，无需启动 Docker 或配置模型密钥。测试文件清单集中在 `scripts/check.py`；新增独立测试时请更新清单。

实际启动、服务配置和模型评估见 [README](README.md)。本地配置从 `.env.example` 复制为 `.env`，不要提交真实密钥。

## 修改与验证

- 保持兼容入口可用；调整目录或命令时同步更新文档和调用方。
- Python 格式化使用 `uv run ruff format .`，检查使用 `uv run python scripts/check.py`。
- 数据库和 Milvus 集成测试需要本地依赖，运行方式见 [README 的测试说明](README.md#测试与评估)。数据库测试使用专用 `aicare_test` 库，会清理测试数据。
- 真实模型评估可能产生费用，不属于默认 CI。PR 中说明已执行的检查、结果和未验证范围。
- 页面改动检查桌面及窄屏布局；SQL 变更保留迁移顺序，并说明已有数据的兼容方式。

## 提交内容

提交源码、测试样例、依赖锁文件、配置示例及文档。不要提交 `.env`、日志、数据库、会话检查点或运行报告。目录规则见 [data/README.md](data/README.md)。

提交前查看 `git status --short` 和 `git diff --cached`，确认没有凭据或用户数据。一次 PR 尽量围绕一个明确问题，写清变更原因和验证方式。历史记录位于 docs/archive（本地文件，不随仓库提交），当前操作以根 README 为准。

## 授权

仓库尚未声明开源许可证。公开托管不等于授予任意使用、修改和分发许可；发布前应由项目所有者选择授权方式。
