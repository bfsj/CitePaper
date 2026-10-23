# 验证记录

初测：2026-10-22；交付复核：2026-10-23。这里只记录实际执行的检查，不把配置文件存在当成部署成功。

## 环境与自动化

Windows / Python 3.13.5；关键依赖版本见 `constraints.txt`，完整验证摘要见 [validation.json](results/validation.json)。

交付复核：72 项测试通过，语句覆盖率 90.75%，Ruff 检查与格式检查通过。`pip wheel --no-deps .` 已成功构建 `cite_paper-0.2.0-py3-none-any.whl`；构建产物保存在本地 `output/wheels/`，不提交 Git。

本轮自动化测试覆盖：

- arXiv Atom 解析、原有模型与 JSON 缓存练习。
- PDF 页文本、原文切片、文档版本、预算和非重叠证据。
- 篡改 quote/hash/page/source/title 的检测，以及伪造引用的隔离。
- 并发领取、幂等提交、队列背压、租约过期与旧持有者写入 fencing。
- 真子进程在 plan checkpoint 后 `os._exit(23)`，另一个进程恢复后跳过已完成规划。
- 慢节点期间的心跳、执行中的取消、配置变更后的恢复保护。
- 认证、租户隔离、SSE 重放、审核 hash 和审批限制。
- 多子问题预算共享、CLI 离线演示和评测输出。

具体测试数量与语句覆盖率以机器可读记录为准。覆盖率不是正确性的证明；尤其 PDF 布局与语义判断需要人工检查。

## 真 HTTP 端到端

不是 TestClient：启动本机 Uvicorn，上传 RAG 论文 PDF，提交后台任务，读取 SSE，再携带 Last-Event-ID 重连，下载 Markdown，写入一次标记“仅联调、未审核语义”的拒绝审核记录。

实测 PDF 上传 201、任务提交 202、执行 succeeded、9 条事件、重连不重复 queued、审核记录 201。原始摘要：[http-smoke.json](results/http-smoke.json)。

复现：

```powershell
python -m cite_paper --database .citepaper/http-smoke.sqlite3 serve --embedded-worker --port 8012
# 另一终端；先完成 fetch-corpus
python scripts/http_smoke.py
```

## 真实论文检索评测

语料：RAG 2005.11401v4、Self-RAG 2310.11511v1、HyDE 2212.10496v1、Lost in the Middle 2307.03172v3。总计 78 个物理 PDF 页。来源、版本、PDF SHA-256 和 12 条手工短跨度标签在 [real_corpus.json](../examples/real_corpus.json)。

先下载，再复现：

```powershell
python -m cite_paper fetch-corpus examples/real_corpus.json
python -m cite_paper evaluate --manifest examples/real_corpus.json --budget 400 --repeats 5
```

所有策略使用相同 PDF 提取、100 单位分块和 BM25。每题预算 400 检索单位；每策略 12 题 × 5 次顺序检索。测量包含本地数据库读取及排序，不包含下载、PDF 解析或 LLM 调用，不是并发负载测试。页缓存、其他 CPU 工作负载会影响延迟。

| 策略 | 原文跨度完整覆盖 | 预算超限 | 原文切片失配 |
| --- | ---: | ---: | ---: |
| Top-K + 标题/章节上下文 | 10/12 | 0 | 0 |
| 邻块扩展 + 上下文 | 9/12 | 0 | 0 |
| RSE + 上下文 | 7/12 | 0 | 0 |
| RSE，不加标题/章节上下文 | 9/12 | 0 | 0 |

完整结果：[summary.md](results/real/summary.md)、[逐题原始指标](results/real/metrics.json)。

失败分析：点状事实常由一个块就能覆盖，扩展块会消耗预算却未增加该题有效覆盖；标题/章节信号可能放大主题相近但不回答当前问题的块；贪心段策略也会过度集中于一篇文档。这里只是观察与待验证解释，没有用事后改参数让这 12 题“刷分”。

默认策略选 Top-K 是小样本下的保守取舍，不是“Top-K 在所有任务上更好”。下一步应建立独立 holdout，并增加跨段、多论文比较型问题。

## 合成回归

3 篇明确虚构的测试文档，12 条短跨度标签。四组设置均为 12/12，不代表真实论文质量；作用是检查核心不变量和生成稳定的离线演示。

[合成结果](results/synthetic/summary.md)。

## 本地真实模型

实际使用已有 Qwen3-0.6B 权重在本机 CPU 上执行 planner 和 writer，没有调用收费模型 API，也没有占用正在忙的远程 GPU。

联调中保留了失败：环境 HTTP proxy 起初使 localhost 返回 502；绕过后完成过一次真实报告；多子问题改造后出现过小模型结构输出失败。为此增加了证据段数量边界、精简输入元数据和更短的写作约束。历史与最终状态见 [model-smoke.json](results/model-smoke.json)，不是用 mock 替代失败。

模型结果只代表模型接入和结构检查，不代表科研结论准确。`semantic_entailment=not_evaluated`，模型辅助报告始终需要人工审核。CPU 小模型可能较慢，测试脚本将单次超时设为 300 秒；不要拿这个适配器声称高吞吐推理。

## PDF 视觉检查

使用 pypdf 提取真实论文，Poppler 渲染并检查 HyDE 第一页；标题、作者和双栏布局可读，与提取文本的列顺序对应。Poppler 报了字体 fallback 警告，渲染页未见明显缺字。没有据此宣称所有 78 页的公式、表格和阅读顺序均正确；无 OCR 和复杂版面恢复保证。

## 未验证或未实现

- Docker/Compose、本地之外的 Linux 运行、GitHub Actions 实际远程执行。
- 自动化语义蕴含验证、模型结论准确率、人工用户研究。
- 生产部署、跨主机扩缩容、压力下吞吐或可用性指标。
- Dense embeddings、向量数据库与神经 reranker。
- 不可信恶意 PDF 的隔离解析和资源沙箱。

当前版本是单机论文调研后端原型，不是已经商用验证的科研平台。
