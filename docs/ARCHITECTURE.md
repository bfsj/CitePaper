# CitePaper 工程设计与边界

## 1. 系统定位

面向 CS 论文的 evidence-first research backend。核心交付是“结论—证据—原文位置”账本、可恢复任务和可复现评测，而不是给聊天界面套一个向量库。

当前版本面向单机和小型论文语料。SQLite WAL、同步模型调用、Python BM25 是当前规模下的工程取舍，不是跨机器生产集群。

```text
CLI / HTTP client
       │ POST /runs (202, run_id)
       ▼
FastAPI ───── SQLite WAL ───── standalone Worker
   │           │                    │
   │           ├─ documents/chunks   ▼
   │           ├─ runs/checkpoints   LangGraph
   │           ├─ events/reviews     plan → discover → retrieve → write → verify → report
   │           └─ network cache      │       │           │
   │                                 │       │           └─ extractive / chat-completions
   └─ SSE: event id + replay          │       └─ BM25 + topk / neighbors / rse
                                     └─ local PDF / allowlisted arXiv PDF
```

## 2. 证据数据模型

- `Paper`：arXiv 元数据对象及 `from_dict` 反序列化接口。
- `Document`：固定版本的提取文本页数组；版本哈希同时关联 PDF 字节哈希和提取文本哈希。
- `Chunk`：文档 ID、页码、章节、起止字符、原文、检索用标题/章节提示。
- `Evidence`：连续原文片段、源文档版本哈希、页码、字符范围、chunk IDs。
- `Claim`：陈述、分类、证据 ID 列表；模型不能提交任意网页作为证据。

页码指 PDF 物理页的 1-based 序号。字符偏移是 **Python 字符索引、相对于单页提取文本、左闭右开**，不是 PDF 字节位置或屏幕坐标。

核心不变量：

```python
evidence.quote == document.pages[evidence.page - 1][evidence.start:evidence.end]
```

提取结果不做去连字符/合并空白等不可逆处理。标题/章节提示只参与排序，不会拼进 quote。章节只识别显式 Markdown/编号标题，无法识别时为 `Unclassified`；这不是完整科学论文版面解析器。

文档 ID 还包含 chunker 版本和块大小，避免同一页以不同块大小导入后在一个索引里混杂旧块。重复相同导入使用 `INSERT OR IGNORE`，原始文档不覆写。

## 3. 三种检索策略

所有策略使用相同语料、BM25、分块大小和预算。

| 策略 | 候选 | 选择方式 |
| --- | --- | --- |
| `topk` | 单块 | 按相关度排序，在预算内装入；这里不是固定 K |
| `neighbors` | 命中块及前后邻块 | 同页同章节内扩展；不重叠、不越预算 |
| `rse` | 长度 1–3 的连续块段 | 累积归一化 BM25，扣除每块 0.18 的上下文成本，再贪心选段 |

RSE 候选受文档、页、章节边界约束。最终片段来自原文切片，不由 LLM 重写。选择是贪心启发式，不保证全局最优。多子问题平分剩余预算，已选片段会排除，防止第一个子问题耗尽整个上下文。调研工作流另设最多 4 个证据段的总上限，并为后续子问题预留名额；独立 `/retrieve` 与检索评测不使用此段数上限。

**预算单位不是模型 token**：`domain.units()` 计英文词、单个汉字、标点，用于无模型依赖的可复现对比。预算只约束引文正文，不包括 system prompt、JSON 元数据和模型回答；真实 API token 用量单独保存在 `usage`。Writer 只接收 ID、标题、页码、章节和 quote；其余审计元数据保存在账本中，以减少模型输入开销。模型最多输出 4 条 claim。

真实小样本评测中 Top-K 优于当前 RSE，所以 API/CLI 默认 Top-K。RSE 保留为实验开关。不要把算法复杂度或来源项目的声誉当成本项目的质量证明。

## 4. LangGraph 与恢复

这是**应用层阶段检查点**，不是 `langgraph-checkpoint-sqlite` 的官方 Saver。StateGraph 负责有类型状态与节点调度，Store 负责把完成节点的状态和进度事件放在同一个 SQLite 事务里。

恢复流程：

1. `BEGIN IMMEDIATE` 原子领取 queued 任务，生成唯一 lease token。
2. 独立心跳线程延长租约；所有 checkpoint/finish 都校验 token 和到期时间。
3. 节点完成后，checkpoint 与 event 同事务提交。
4. Worker 异常退出后，其他 Worker 发现租约过期，重新领取任务。
5. 从最后完成节点的**下一个节点**继续。尚未完成节点会重跑。
6. 默认最多 3 次租约领取；超过上限标记 failed。

恢复后会校验模型名称、端点和 pipeline 版本的配置指纹，避免一个任务前后混用不同模型。不要在存在活跃任务时直接改变处理代码/数据库布局；当前版本没有通用 schema migration 框架。

语义是 **at-least-once**。PDF 导入有幂等标识；外部模型请求仍可能在崩溃后重复并产生费用。checkpoints 不承诺 exactly-once。HTTP 重试最多两次，仅对连接/超时和指定瞬时状态码重试；结构错误直接失败。

## 5. 状态与事件

```text
queued ──claim──► running ──finish──► succeeded / failed
   │                │
 cancel          cancel_requested ──安全边界──► cancelled
                    │
                 lease expired ──► queued / failed / cancelled
```

取消是协作式的，不强杀线程。正在进行的 PDF 解析或网络请求需要先返回，最长等待受 provider timeout 限制。取消后不能提交成功结果。

事件 ID 是数据库全局递增序号，SSE 通过 `Last-Event-ID` 重放该任务的后续事件；ID 无须在单个任务内连续。事件先持久化，再推送。断开 SSE 不会取消任务。

`Idempotency-Key` 在 tenant 内唯一：同 key + 同请求返回同任务；不同请求返回 409。每 tenant 默认最多 30 个 queued/running 任务，超出返回 429。

## 6. 核验与人工审核

自动检查：文档存在、版本哈希、页码、字符范围、原文 quote 一致、标题/来源一致、引用 ID 存在。无效引用的 claim 不进入展示报告，并记录问题。

**没有自动实现语义蕴含判定**。`semantic_entailment=not_evaluated` 始终如实保留。模型报告标记 `needs_review=true`；extractive 模式只整理原文，不冒充模型比较结论。

人工审核单独记录在 append-only reviews 表，绑定报告 SHA-256；旧 hash 返回 409，有结构核验错误不能 approve。审核不会把 `not_evaluated` 改成“算法已证实”。当前是 tenant 级密钥权限，不是区分 reviewer/admin 的完整 RBAC。

## 7. 安全与运维

- API keys 映射到可信 tenant，调用者不能通过自填 tenant header 越权。
- 未配置密钥只用于 localhost demo；CLI 拒绝无密钥的公网 bind。
- PDF 下载只接受严格 arXiv ID，每次重定向检查 HTTPS、host、port、path。
- 请求体、PDF 字节、页数、提取文字长度、检索预算均有限制。
- arXiv 元数据缓存 24 小时；同数据库的跨进程调度槽间隔 3 秒。
- LLM 端点只由服务端配置；不继承环境 HTTP proxy，防止凭据被意外转发。
- 输出 source 内容放在安全代码围栏里；报告作为文本/Markdown 返回，不提供 HTML 执行器。
- `X-Request-ID`、run ID、阶段耗时、模型 usage、状态事件和 `/stats` 提供基础观测。
- 日志不写密钥、源文全文和 provider response body。数据库包含用户论文和生成内容，需做好磁盘权限与备份。

不应直接把这个 demo 开放给不可信互联网用户：没有 OCR、恶意 PDF 沙箱、账户系统、跨主机队列、完整限流/存储配额/删除策略。PDF 大小限制也不能完全阻止解压/解析型拒绝服务。正式部署应增加隔离解析容器、认证网关、TLS、资源限制和迁移方案。

## 8. 参考与原创边界

- [dsRAG RSE 源码](https://github.com/D-Star-AI/dsRAG/blob/main/dsrag/rse.py)：启发了连续证据段思路。本项目重新实现 BM25 + 检索单位预算版本，不是 dsRAG 完整移植；标题/章节上下文也不是其生成式 AutoContext 的复刻。
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)：使用官方 StateGraph。
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：帮助界定重跑和副作用边界；本项目使用自有 Store 检查点。
- [Aegra](https://github.com/aegra/aegra)：参考任务队列与 worker 分离的架构思想，没有复制其运行时或实现 Agent Server 协议。

上述设计参考与算法概念不属于本项目的原创发明。本项目的实现范围是证据数据契约、预算策略、工作流集成、持久化任务、失败恢复、租户隔离、核验、评测与测试。
