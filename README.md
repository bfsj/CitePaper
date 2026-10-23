# CitePaper

**证据可追溯的论文调研与异步任务后端**。基于 RAG、LangGraph 与 FastAPI，提供从论文正文检索到调研报告的可复现工作流。

它把“找论文”扩展成一个完整链路：PDF 导入 → 正文检索 → 引用账本 → 调研报告 → 人工复核；任务独立于 HTTP 请求运行，断线和 Worker 异常退出都有明确处理方式。

## 已实现

- LangGraph 六阶段流程：plan / discover / retrieve / write / verify / report。
- PDF/UTF-8 文档导入，固定版本快照，页码、章节和字符偏移定位。
- BM25 + Top-K、邻块扩展、连续证据段 RSE；统一预算和上下文消融。
- 证据 quote 与原文逐字符核验、引用 ID 校验、证据不足标记。
- FastAPI 202 异步任务、独立 Worker、SQLite WAL 持久化和阶段检查点。
- 租约心跳、过期恢复、旧 Worker 写入隔离、幂等提交、取消与 SSE 事件重放。
- API key → tenant 隔离、上传限制、arXiv 下载白名单、缓存及跨进程限速。
- 报告哈希绑定的人工审核记录；阶段耗时、模型 usage、任务统计。
- 离线演示、真实论文小型评测、自动化测试与 CI/Docker 模板。

**边界也明确：**当前默认是稀疏检索，不是向量检索；自动核验来源，不证明语义正确；SQLite 是单机方案，不是分布式生产系统。RSE 的设计受 dsRAG 启发，独立实现和取舍见 [架构说明](docs/ARCHITECTURE.md)。

## 先跑起来

Python 3.11+，在仓库根目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -c constraints.txt -e ".[dev]"

python -m cite_paper demo
python -m cite_paper serve --embedded-worker
```

- 离线报告：`output/demo-report.md` 和同名 JSON。
- 已生成的示例：[demo-report.md](docs/examples/demo-report.md)。
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)。
- 此演示使用**明确标注的合成文档**，不下载模型、不调用收费 API、不把虚构结果当论文。
- `demo` 强制 extractive 模式；真实模型辅助流程使用 `run` 或 API。

服务拆分为两个进程：

```powershell
# 终端 A；两个终端必须使用同一个绝对数据库路径
$env:CITEPAPER_DATABASE = 'C:\SD\SN\CitePaper\.citepaper\service.sqlite3'
python -m cite_paper serve

# 终端 B：设置相同的 CITEPAPER_DATABASE
python -m cite_paper worker
```

不激活虚拟环境时，把 `python` 替换为 `.\.venv\Scripts\python.exe`。

## 导入自己的论文并研究

```powershell
python -m cite_paper import .\my-paper.pdf --title "My paper"
# 复制上一步输出的 id
python -m cite_paper run "What method and limitations does this paper report?" `
  --documents YOUR_DOCUMENT_ID --wait --output output/my-report.md
```

不加 `--wait` 只提交任务，需单独运行 worker。默认 `--strategy topk`；可以改为 `neighbors` 或 `rse` 做对照。没有模型配置时，报告是**原文证据摘录**，不是模型撰写的综述。

通过 HTTP：

```powershell
$base = 'http://127.0.0.1:8000'
$doc = Invoke-RestMethod "$base/documents/text" -Method Post -ContentType 'application/json' `
  -Body (@{ title='Study'; text='Evidence retrieval uses immutable source spans.' } | ConvertTo-Json)

$body = @{ topic='evidence retrieval'; document_ids=@($doc.id); budget=600 } | ConvertTo-Json
$run = Invoke-RestMethod "$base/runs" -Method Post -ContentType 'application/json' `
  -Headers @{ 'Idempotency-Key'='study-001' } -Body $body

Invoke-RestMethod "$base/runs/$($run.id)"
curl.exe -N "$base/runs/$($run.id)/events"
# 重连：curl.exe -N -H "Last-Event-ID: 3" "$base/runs/$($run.id)/events"
Invoke-WebRequest "$base/runs/$($run.id)/report" -OutFile output/http-report.md
```

主要接口：

| 接口 | 用途 |
| --- | --- |
| `GET /health`, `GET /ready` | 进程/数据库状态 |
| `POST /documents/text`, `POST /documents/pdf` | 导入文本/PDF，PDF 用 multipart |
| `GET /documents`, `GET /documents/{id}` | 列出文档、读取原始页文本 |
| `POST /retrieve` | 单独观察检索证据 |
| `POST /runs`, `GET /runs/{id}` | 提交任务、查看状态与 JSON 结果 |
| `GET /runs/{id}/events` | SSE 进度，支持 Last-Event-ID |
| `POST /runs/{id}/cancel` | 协作式取消 |
| `GET /runs/{id}/report` | Markdown 报告 |
| `POST/GET /runs/{id}/reviews` | 绑定 report_hash 的人工审核/审计 |
| `GET /stats` | 当前 tenant 的文档与任务状态计数 |
| `POST /papers/search` | 可选 arXiv 元数据检索 |

人工审核请求示例，`report_hash` 从任务状态接口获取：

```json
{"report_hash":"64-character-sha256-from-status","decision":"approve","note":"已逐条复核原文与比较结论"}
```

审核是人的判断，不会把系统里的 `semantic_entailment=not_evaluated` 伪装成自动验证通过。

## 配置真实模型

使用支持 `/chat/completions` 的服务，base URL 一般以 `/v1` 结尾：

```powershell
$env:CITEPAPER_LLM_BASE_URL = 'http://127.0.0.1:8011/v1'
$env:CITEPAPER_LLM_MODEL = 'Qwen3-0.6B'
# 服务需要密钥时再设置 CITEPAPER_LLM_API_KEY；不要提交到 Git
$env:CITEPAPER_LLM_TIMEOUT = '150'  # CPU 小模型测试用；默认 45 秒
python -m cite_paper run "Compare retrieval methods and their limitations" `
  --documents DOC_ID_1 DOC_ID_2 --wait
```

本地模型测试适配器是可选项，默认安装不含 PyTorch：

```powershell
# CPU 测试先装 CPU 轮子，避免无意下载 CUDA 依赖
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[local]"
python scripts/local_model_server.py --model C:/SD/SN/models/Qwen3-0.6B --port 8011
# 另一终端设置上面的模型环境变量后：
# 先完成下文的 fetch-corpus，准备真实论文后再运行：
python scripts/model_smoke.py
```

`local_model_server.py` 只绑定 localhost、串行推理、只加载已有本地权重，是联调用途，不替代 vLLM。已实际跑通 Qwen3-0.6B CPU 模型调用，但该模型的研究结论质量有限，需要复核；详见 [验证记录](docs/VALIDATION.md)。

## arXiv 与真实论文评测

API/工作流的自动发现默认禁用，需要显式设置：

```powershell
$env:CITEPAPER_ALLOW_ARXIV = '1'
python -m cite_paper search "retrieval augmented generation" --max-results 3
# 自动发现至多 2 篇候选并下载 PDF
python -m cite_paper run "retrieval augmented generation" --discover --wait
```

固定真实语料包含 RAG、Self-RAG、HyDE、Lost in the Middle 的指定 arXiv 版本。PDF 只存到 Git 忽略的数据目录；仓库保存来源、版本、文件哈希和短证据标签，不重新发布论文全文。

```powershell
python -m cite_paper fetch-corpus examples/real_corpus.json
python -m cite_paper evaluate --manifest examples/real_corpus.json `
  --budget 400 --repeats 5 --output output/real-evaluation
# 无需网络的合成回归集：
python -m cite_paper evaluate
```

[真实结果](docs/results/real/summary.md)：4 篇论文、12 条人工证据跨度，400 检索单位预算下，Top-K 命中 10/12，邻块扩展 9/12，当前 RSE 7/12，去掉标题/章节上下文的 RSE 为 9/12。因此当前默认 Top-K，**没有宣称 RSE 带来提升**。

这是小型开发回归，不是无偏公开 benchmark；不测回答事实正确率。检索单位也不是模型 tokenizer 的 token。完整口径、原始结果和限制见 [验证记录](docs/VALIDATION.md)。

## 测试与工程文件

```powershell
python -m pytest -q --cov=cite_paper
python -m ruff check src tests scripts
python -m ruff format --check src tests scripts
```

```text
src/cite_paper/
  api.py / cli.py       HTTP 与命令行入口
  ingest.py / domain.py 固定版本、分页和证据数据契约
  retrieval.py          BM25 与预算片段选择
  workflow.py           LangGraph 节点与恢复路由
  store.py / worker.py  持久化任务、租约、事件、审核
  providers.py          结构化模型适配器
  network.py            arXiv 缓存/跨进程请求节奏
  evaluation.py         同预算评测与原始记录
tests/                  离线回归与故障注入
scripts/                本地模型和真实 HTTP 联调
examples/real_corpus.json 固定版本语料与人工标签
docs/                   架构、运维、接口与验证记录
```

`constraints.txt` 记录验证过的关键依赖版本，不是完整锁文件。CI 为 Ubuntu/Python 3.11、3.13 提供配置；Docker Compose 为单主机部署提供模板。**本次只验证了本机 Windows 环境，未把模板视为已在线部署。**

## 项目文档

- [架构和安全边界](docs/ARCHITECTURE.md)
- [运行与故障定位](docs/OPERATIONS.md)
- [实际验证记录](docs/VALIDATION.md)

`models.py` 提供 arXiv 元数据对象；服务运行时使用 `network.py` 的数据库缓存。`cache.py` 保留轻量 JSON 缓存接口，不用于并发服务。
