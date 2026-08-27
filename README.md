# CitePaper

CitePaper 是一个面向计算机科学论文调研的可验证 Deep Research Agent。当前里程碑先完成最小闭环：输入研究主题，调用 arXiv API，并返回可以继续交给 Agent 使用的结构化论文元数据。

## 当前能力

- 根据自然语言主题搜索 arXiv。
- 解析标题、作者、摘要、分类、发布日期、摘要页和 PDF 链接。
- 提供命令行和 FastAPI 两种入口。
- 对 Atom XML 解析、参数校验和查询构造提供单元测试。
- 不需要模型 API Key 即可完成第一阶段。

## 项目结构

```text
CitePaper/
├── src/cite_paper/
│   ├── api.py
│   ├── cli.py
│   ├── models.py
│   └── tools/arxiv.py
├── tests/
├── pyproject.toml
└── README.md
```

## 本地安装

在 PowerShell 中运行：

```powershell
git clone https://github.com/bfsj/CitePaper.git
cd CitePaper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## 命令行使用

```powershell
cite-paper search "retrieval augmented generation" --max-results 5
```

也可以不安装项目，直接从源码运行：

```powershell
$env:PYTHONPATH = "src"
python -m cite_paper.cli search "retrieval augmented generation" --max-results 5
```

## 启动 API

```powershell
fastapi dev
```

启动后访问：

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

搜索请求示例：

```json
POST /papers/search
{
  "query": "long context retrieval augmented generation",
  "max_results": 5
}
```

## 运行测试

```powershell
pytest
```

当前测试不访问网络，使用固定 Atom XML 验证解析结果。

## 下一阶段

1. 增加查询规划节点，把研究问题拆成 3 至 5 个子问题。
2. 使用 LangGraph 串联 Planner、Researcher、Verifier 和 Writer。
3. 增加 PDF 下载、正文提取和证据片段定位。
4. 建立固定评测集并使用 MLflow 保存 Trace、参数和指标。
5. 增加 SQLite checkpoint、失败重试和人工确认。

## arXiv 使用注意事项

- 相同查询应缓存，避免无意义重复请求。
- 连续调用 API 时应至少间隔 3 秒。
- 当前工具将单次结果数限制在 1 至 50 条。
- 项目进入持续运行阶段前，应补充本地缓存和全局限速器。
