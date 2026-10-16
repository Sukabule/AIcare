# Milvus 启动恢复 · 2026-10-14

## 问题与处理

原 `milvusdb/milvus:v2.6.23` 在初始化会话时调用内置 etcd，日志显示请求跨过首次选主，随后 `panic: etcdserver: leader changed`，容器退出134。缩短选主间隔仍复现，因此撤回该尝试，不依靠时序碰运气。

`compose.yaml` 增加独立 `quay.io/coreos/etcd:v3.5.23`（与原内置版本相同），Milvus 使用 `ETCD_USE_EMBED=false`、`ETCD_ENDPOINTS=etcd:2379`；`depends_on` 要求 etcd 的 `etcdctl endpoint health` 成功后才启动 Milvus。Milvus镜像、存储和消息队列选择均保留。

etcd 与 Milvus 复用原 `aicare_milvus-data` 卷，etcd 仍只在 `/var/lib/milvus/etcd` 读写旧元数据，保留原 member 身份与 peer URL。恢复默认选主参数，etcd 不发布宿主机端口。先停旧 Milvus 再启独立 etcd，避免两个进程同时写元数据。

停机元数据备份：`.cache/backups/milvus-etcd-20261014.tar.gz`（1,343,779字节）；原 Compose 与配置也已备份。没有执行清库、重建集合、付费重新嵌入。

官方依据：[Milvus 2.6.23 Compose](https://raw.githubusercontent.com/milvus-io/milvus/v2.6.23/deployments/docker/standalone/docker-compose.yml)、[etcd 调优说明](https://etcd.io/docs/v3.5/tuning/)。本项目保留本地对象存储，不另外引入MinIO；原卷是原文派生数据和元数据的持久来源。

## 检查

- Compose 配置通过校验，etcd 和 Milvus 健康。
- `/api/kb/overview`：MySQL total52/done52/pending0，Milvus52，consistency=consistent。
- 使用 Ch04 已保存的嵌入缓存（无新API请求）测试「那个邮费到底要给多少钱啊」：dense / BM25 / hybrid 第一条均为「运费怎么算」；dense相似度0.7263，答案含满99元包邮说明。
- 检索证据：`log/acceptance/milvus-recovery.json`。
- 此前失败的两项集成测试补跑均通过（6.45秒）：中文BM25/混合检索/过滤，以及MySQL-Milvus中断恢复。结合前次200项通过，202项保留功能测试均已有通过记录；没有为修复重跑付费评估。
- etcd和Milvus完整停机后再次启动，两者均healthy；再次盘点仍为52/52、pending0，六个保留页面200，两个后台分类入口404。Ruff和离线锁文件检查通过。
- 管理模块仍为知识库、审核队列、问题池占位、RAG评估、观测与成本；没有恢复后台主题分类或训练。
