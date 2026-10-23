# 运行与故障定位

## 两个进程

开发演示可以 `python -m cite_paper serve --embedded-worker`。更接近实际后端的方式是 API 和 worker 分开启动，两个进程的数据库必须指向**同一绝对路径**：

```powershell
$env:CITEPAPER_DATABASE = 'C:\SD\SN\CitePaper\.citepaper\service.sqlite3'
python -m cite_paper serve
# 第二个终端设置同一个 CITEPAPER_DATABASE 后：
python -m cite_paper worker
```

API 在 Worker 不在线时仍可接收 queued 任务。这是持久化队列的行为；`/ready` 只检查 DB，不代表 Worker 或模型可用。`/health` 展示是否启用认证和模型。

## 常见现象

| 现象 | 首先检查 |
| --- | --- |
| 一直 queued | worker 是否运行、数据库绝对路径是否一致 |
| 失败只有 ProviderError | 模型服务是否启动、是否为正确的 `/v1` base URL、结构化输出和超时 |
| CPU 小模型写作超时 | 设置 `CITEPAPER_LLM_TIMEOUT=150`；不要误以为 4090 正在参与计算 |
| 证据为空 | 关键词是否与英文论文匹配、预算是否容纳完整块、文档是否属于当前 tenant |
| 401 / 404 | Bearer key 是否正确；跨 tenant 资源故意返回 404 |
| 409 | 幂等键被不同请求复用、报告尚未完成，或 review hash 过期 |
| 429 | 当前 tenant queued/running 任务达到上限，稍后重试 |
| PDF 422 | 文件是否真正 PDF、是否加密/扫描件、页数/文字长度是否超限 |
| SSE 看似卡住 | 检查代理 buffering，使用 `curl.exe -N`，先从状态接口查看任务 |
| 端口已被占用 | `--port` 换一个；不要结束不属于本项目的进程 |

## 备份与持久化

数据库在 `.citepaper/`，默认被 Git 忽略。运行中不要仅复制主 sqlite3 文件而遗漏 WAL。可以停止 API/worker 后备份，或使用 Python sqlite3 的在线 `backup()` 接口。数据库保存论文文本和生成内容，按用户数据保护；不要上传到公开 GitHub。

当前没有通用迁移工具。改变分块/流程/数据库 schema 前先备份，完成现有任务，在新数据库里做实验。业务失败需要重新提交；自动恢复主要针对 Worker 租约丢失，不能把任意失败无限重试。

## 部署模板

`Dockerfile` 与 `compose.yaml` 是单主机模板，API/worker 共用 Docker named volume。先复制 `.env.example` 为 `.env` 并替换随机密钥，再 `docker compose up --build`。本机没有 Docker，本次没有把这一步当成已实测结果。

外部访问前添加 TLS/反向代理、认证和调用配额；保持 volume 位于本地磁盘，不使用 NFS/网络盘承载 SQLite WAL。模型服务在容器外时需设置容器实际能访问的地址，`127.0.0.1` 在容器里并不指宿主机。

可增加 worker 进程数验证单主机并发，但 SQLite 仍是单写入者。数据量和并发增长后，再评估 Postgres、向量索引以及独立解析服务，而不是直接宣称分布式生产就绪。
