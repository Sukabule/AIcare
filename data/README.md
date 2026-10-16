# 数据目录

| 路径 | 内容 | 默认提交 |
|---|---|---|
| `kb/` | 演示知识材料，知识建设的输入 | 是 |
| `ch03/reports/` | 检索及会话挖掘生成报告 | 否 |
| `ch04/reports/` 至 `ch09/reports/` | 各阶段评估与观测报告 | 否 |
| `checkpoints/`、根目录 `*.db` | 会话状态及 SQLite 数据 | 否 |
| `ch10/` | 已撤销分类器的本地历史语料 | 否 |

忽略规则只控制 Git 提交，不删除本地文件。测试输入保存在 `tests/data/`。

新克隆默认没有运行报告，应先按根 README 配置依赖，再按需运行评估；涉及真实模型的任务可能产生费用。不要把本地评估结果当成所有新环境都已通过的验证记录。

Ch03 输出由以下命令生成，脚本会自动创建报告目录：

```bash
uv run python scripts/kb_cli.py eval-retrieval
uv run python scripts/eval_mining.py
```
