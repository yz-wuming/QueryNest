# QueryNest

**QueryNest — 多模态文档智能与 RAG**

QueryNest 是一个面向复杂文档的多模态 **检索增强生成（RAG）** 系统。它在 [RAG-Anything](https://github.com/HKUDS/RAG-Anything)（MIT License）之上进行了**产品化二次开发**：分层重构、品牌重塑，并新增了一整套 Query / Retrieval / Citation / Evaluation / API / CLI / 文档管理能力，以及一个浏览器端 Web 控制台。

---

## 1. 项目概述

面对 PDF / 图片 / Word / Excel / PPT 等复杂文档，传统文本 RAG 会丢失图片、表格、公式中的关键信息。QueryNest 的目标是打通完整闭环：

```
文档进入 → 多模态解析 → 结构化索引 → 混合检索 → 重排 → 上下文构造 → LLM/VLM 生成 → 可引用回答
```

```
Document ──▶ Parsing ──▶ Multimodal ──▶ Indexing ──▶ Hybrid Retrieval
                                                          │
                                                        ┌─▶ Dense（向量）
   LLM/VLM ──▶ Citation ◀── Context Construction ◀── ──┤─▶ Keyword（BM25）
                                                        │
                                              Reranker ◀┘─▶ Graph（知识图谱）
```

## 2. 核心特性

| 能力 | 说明 |
| --- | --- |
| 多模态解析 | 继承 MinerU / Docling / PaddleOCR，解析 PDF / 图片 / Office 文档 |
| 多模态处理器 | 图片理解、表格结构化、公式（LaTeX / OMML）提取 |
| Graph-RAG | 继承 LightRAG，实体/关系知识图谱检索 |
| Query Analyzer | 判断问题类型：TEXT / IMAGE / TABLE / EQUATION / MULTIMODAL / CROSS_DOCUMENT |
| Query Rewrite | 多轮上下文下的问题改写（指代消解） |
| Hybrid Retrieval | Dense + Keyword(BM25) + Graph 多路召回、RRF 融合、去重、重排 |
| Reranker | 可插拔 `BaseReranker` 抽象 + `BGEReranker` / `NoopReranker` |
| Citation | 结构化引用来源 `[N] document.pdf — Page 4` |
| Multimodal Context Builder | 文本 / 图片 / 表格 / 公式统一为结构化上下文 |
| 文档管理 | `list` / `get` / `delete` / `exists` / `status` 知识库管理 |
| RAG 评测 | Recall@K / MRR / Precision@K / NDCG@K / Faithfulness / Answer Relevancy |
| CLI + FastAPI | `querynest` 命令行 与 REST API |
| Web 控制台 | 浏览器端单页面 UI（Chat / Documents / Evaluation / API / Settings） |
| 统一配置 | `QUERYNEST_*` 环境变量前缀，`.env` 支持 |

## 3. 架构

```
querynest/
├── __init__.py          # 公开 API 出口
├── cli.py, __main__.py  # CLI（querynest ingest/query/documents/evaluate/serve）
├── core/
│   ├── config.py        # QueryNestConfig（QUERYNEST_* 环境变量）
│   ├── engine.py        # QueryNest 核心引擎（门面 + 内部 RAGAdapter）
│   ├── models.py        # 统一数据模型：ContextItem / Citation / DocumentMetadata / RetrievalResult
│   ├── exceptions.py    # 统一异常体系
│   └── logging.py       # 统一日志（querynest.*）
├── ingestion/           # 解析 / 批处理（继承 RAG-Anything）
├── multimodal/          # 图片 / 表格 / 公式处理器（继承 + OMML 公式提取）
├── retrieval/
│   ├── hybrid.py        # 多路召回 + RRF 融合 + 去重 + 重排
│   ├── keyword.py       # 纯 Python BM25（QueryNest 新增）
│   ├── reranker.py      # BaseReranker / BGEReranker / NoopReranker
│   └── context.py       # Multimodal Context Builder
├── query/
│   ├── analyzer.py      # Query Analyzer
│   ├── rewrite.py       # Query Rewrite
│   ├── citation.py      # Citation 系统
│   └── base.py          # 继承的查询 mixin
├── evaluation/          # 评测：metrics / dataset / runner
├── storage/
│   ├── cache.py         # 缓存
│   └── document_store.py# 文档管理（JSON 索引 + 磁盘正文）
├── api/
│   ├── server.py        # FastAPI 服务
│   └── static/index.html# Web 控制台（单文件前端，无第三方依赖）
└── utils.py 等
```

## 4. 多模态流水线

1. **解析**（Parser）：MinerU / Docling / PaddleOCR 将文档转为结构化 `content_list`。
2. **多模态理解**：图片 → 视觉模型描述；表格 → 结构化 `table_body`；公式 → LaTeX（含 DOCX 的 OMML 提取）。
3. **索引**：解析结果连同文档元数据写入向量库 / 图数据库（LightRAG）。
4. **元数据关联**：每个 chunk / image / table / equation 关联 `document_id / page / content_type`。

## 5. 检索流水线

```
Hybrid Retrieval ── RRF 融合 ── 去重 ── Rerank ── Final Context
```

- **Dense**：LightRAG `local` 模式向量检索
- **Keyword**：纯 Python BM25（在已入库文档来源文本上）
- **Graph**：LightRAG `global` 模式知识图谱检索
- 融合策略 `rrf`（默认）或 `score`，命中经去重后按分数排序，可选用 Reranker 重排。

> **优雅降级**：当某个检索后端不可用（如未启用图索引）时，只记录 warning 并回退到
> 其余可用路由（Dense + BM25 → Fusion → Rerank），不会导致整条查询崩溃，也绝不伪造图命中。

## 6. 重排

通过统一抽象 `BaseReranker` 实现可插拔重排：

- `BGEReranker`：基于 FlagEmbedding 的 cross-encoder 重排（未安装 FlagEmbedding 时自动回退）；
- `NoopReranker`：原序透传（默认，无额外大模型依赖）。

```python
from querynest.retrieval.reranker import BGEReranker, NoopReranker
reranker = BGEReranker() if BGEReranker.available() else NoopReranker()
```

未强制安装巨型模型——基础 `pytest` 套件在无 FlagEmbedding 时也能通过。

## 7. 引用（Citation）

每个 `RetrievalResult` 携带结构化 `Citation`：`document_id / document_name / page /
chunk_id / content_type / score / source`。LightRAG 现有接口无法给出精确页码时，
`page` 如实置空（或标记 `page_unavailable`），**绝不让 LLM 自行编造页码**。

```python
for i, src in enumerate(result.sources):
    print(f"[{i}] {src.document_name} — Page {src.page or 'n/a'}")
```

## 8. 文档管理

内置 JSON 白名单索引 + 磁盘正文，提供 `list / get / delete / exists / status`。
删除操作会移除元数据与缓存来源文本；受 LightRAG 限制，底层向量 / 图索引不在本层
一并清理，API 会明确返回 `metadata_deletion: done` + `index_cleanup: pending`，
避免造成数据不一致。

## 9. 端到端示例

脚本 [examples/quickstart.py](examples/quickstart.py) 提供一条命令的最小演示
（PDF / 文本 → Parse → Index → Query → Citation）：

```bash
# 1) 配置 .env（参考 .env.example）
# 2) 准备一个文档（文本 demo 用 examples/data/sample.txt）
python examples/quickstart.py examples/data/sample.txt
```

输出示例：

```
Document ingestion started
Parsing completed
Indexing completed
Question:
  QueryNest 的混合检索包含哪几条召回路径？它们如何融合？
Answer:
  根据提供的文档内容，QueryNest 的混合检索包含…… 通过 RRF 融合……
Sources:
  [1] lightrag — n/a
  [2] sample.txt — n/a
```

## 10. 安装

```bash
# 完整安装（含 MinerU 解析器）
pip install -e .

# 仅核心（含 API/CLI，不含重型解析器）可按需安装扩展
pip install -e ".[api]"       # QueryNest API
pip install -e ".[reranker]"  # 可选 BGE reranker
# 可选增强解析器
pip install -e ".[paddleocr]" # OCR
pip install -e ".[all]"
```

> 说明：`mineru` / `lightrag-hku` 为继承自 RAG-Anything 的重型运行时依赖。
> QueryNest 新增的 Query / Retrieval / Evaluation / Storage / CLI 模块为纯 Python，
> 可在无这些重型依赖的情况下独立测试与使用。

## 11. 配置

配置统一使用 `QUERYNEST_` 前缀，见 [.env.example](.env.example)。复制并填写即可：

```bash
cp .env.example .env
```

常用变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QUERYNEST_LLM_API_KEY` | | LLM API Key（必填，建议用 glm-4-flash 等限流宽松的免费模型） |
| `QUERYNEST_LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 接口地址 |
| `QUERYNEST_LLM_MODEL` | `gpt-4o` | LLM 模型 |
| `QUERYNEST_EMBEDDING_MODEL` | `bge-m3:latest` | Embedding 模型 |
| `QUERYNEST_RERANKER_MODEL` | | 重排模型（留空则禁用重排） |
| `QUERYNEST_STORAGE_DIR` | `./querynest_storage` | 存储目录 |
| `QUERYNEST_PARSER` | `mineru` | 解析器：mineru / docling / paddleocr |
| `QUERYNEST_ENABLE_IMAGE_PROCESSING` | `true` | 图片处理开关 |
| `QUERYNEST_ENABLE_TABLE_PROCESSING` | `true` | 表格处理开关 |
| `QUERYNEST_ENABLE_EQUATION_PROCESSING` | `true` | 公式处理开关 |
| `QUERYNEST_QUERY_TOP_K` | `20` | 顶层检索命中数 |
| `QUERYNEST_ENABLE_RERANK` | `false` | 是否启用重排 |

未设置 `QUERYNEST_` 变量时，会回退读取原有名称（如 `LLM_MODEL`）以兼容旧配置。
`.env` 含真实密钥，已被 `.gitignore` 排除，**请勿提交到版本库**。

## 12. 快速开始

```python
import asyncio
from querynest import QueryNest, QueryNestConfig


async def main():
    config = QueryNestConfig()          # 或从 .env / 环境变量读取
    engine = QueryNest(
        config,
        llm_model_func=...,
        embedding_func=...,
    )

    # 文档入库
    await engine.ingest("paper.pdf")

    # 查询
    result = await engine.query("这个表格中哪个模型效果最好？")
    print(result.answer)
    for src in result.sources:
        print(f"  {src.display()}")     # 例如 "[1] paper.pdf — Page 4; Table"


asyncio.run(main())
```

## 13. CLI 用法

CLI 命令统一为 `querynest`：

```bash
querynest ingest document.pdf                          # 入库文档
querynest query "这个表格中哪个模型效果最好？"          # 查询
querynest documents list                               # 列出文档
querynest documents get <id>                           # 获取文档
querynest documents delete <id>                        # 删除文档
querynest evaluate evaluation/datasets/example.json    # 评测
querynest serve                                        # 启动 API 服务
```

## 14. API 与 Web 控制台

启动服务（默认监听 `0.0.0.0:9621`）：

```bash
querynest serve
```

启动后：

- **Web 控制台**：浏览器访问 <http://127.0.0.1:9621>，提供 Chat / Documents / Evaluation / API / Settings 五个页面。
- **交互式 API 文档**：访问 <http://127.0.0.1:9621/docs>（Swagger UI）。

### 主要端点

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查 |
| `GET` | `/documents` | 列出所有已入库文档 |
| `POST` | `/documents` | 按文件路径入库（`path` 必填） |
| `POST` | `/documents/upload` | 直接上传文件（`filename` + base64 `content`） |
| `GET` | `/documents/{id}` | 获取单个文档详情 |
| `DELETE` | `/documents/{id}` | 删除文档 |
| `POST` | `/query` | 检索增强问答 |
| `POST` | `/query/multimodal` | 多模态问答（可附图片） |
| `GET` | `/api/evaluation` | 获取评测指标 |
| `GET` | `/api/settings` | 获取运行配置 |

### 示例

```bash
# 入库
curl -X POST localhost:9621/documents -H "Content-Type: application/json" \
     -d '{"path": "paper.pdf"}'
# 查询
curl -X POST localhost:9621/query -H "Content-Type: application/json" \
     -d '{"query": "哪个模型效果最好？"}'
# 健康检查
curl localhost:9621/health
```

统一响应结构：

```json
{
  "answer": "...",
  "sources": [{"document": "paper.pdf", "page": 4, "type": "table", "score": 0.91}],
  "retrieval": {"num_hits": 10, "intent": "table"},
  "metadata": {}
}
```

## 15. 评测（Evaluation）

数据集格式（JSON 或 JSONL），参考 `evaluation/datasets/` 下的示例：

```json
{
  "question": "...",
  "expected_answer": "...",
  "expected_sources": ["paper.pdf#4", "paper.pdf#8"]
}
```

```bash
querynest evaluate evaluation/datasets/sample.json
```

输出 `evaluation/results.json`，包含 **Recall@5、Recall@10、MRR、Precision@K、NDCG@K**；
Faithfulness / Answer Relevancy 在未注入判定器 / 嵌入函数时如实标记为跳过或启发式，**不虚构数值**。
Web 控制台的 Evaluation 页面会直接展示这些指标。

## 16. 支持的多模态

- **文本** Text
- **图片** Image（视觉模型描述 / base64 多模态查询）
- **表格** Table（结构化 `table_body`）
- **公式** Equation（LaTeX，含 DOCX OMML 提取）
- **跨文档** Cross-Document（多文档对比 / 综述）
- **多模态组合** Multimodal

## 17. 技术栈

- **解析**：MinerU / Docling / PaddleOCR
- **图-RAG**：LightRAG（LightRAG-HKU）
- **LLM/VLM**：OpenAI 兼容 API / Ollama 等（`llm_model_func` / `vision_model_func` 注入）
- **Embedding**：OpenAI / Ollama / bge 等（`embedding_func` 注入）
- **重排**：FlagEmbedding / sentence-transformers CrossEncoder（可选）
- **检索融合**：RRF（Reciprocal Rank Fusion）+ BM25（纯 Python 实现）
- **前端**：单文件原生 JS SPA（无第三方依赖，`querynest/api/static/index.html`）
- **API**：FastAPI + Pydantic
- **CLI**：argparse；**日志**：logging（`querynest.*`）

## 18. Benchmark

暂无权威 benchmark 数值。评测**框架**已实现并可通过
`querynest evaluate evaluation/datasets/sample.json` 运行，输出
Recall@5 / Recall@10 / MRR / Precision@K / NDCG@K（见 §15）。未在大规模公开数据集上做过官方评测，
因此这里**如实声明 "Evaluation framework implemented"**，不虚构精确率数字。

## 19. Roadmap

- [x] Web 控制台（Chat / Documents / Evaluation / API / Settings）
- [x] 多模态问答（/query/multimodal）
- [ ] Faithfulness / Answer Relevancy 的 LLM 判定器开箱集成
- [ ] 文档删除时同步清理图 / 向量索引
- [ ] 增量索引与文档更新的增量处理

## 20. 许可与署名

QueryNest 是在 [RAG-Anything](https://github.com/HKUDS/RAG-Anything)
（Copyright (c) HKUDS，MIT License）基础上的**二次开发**。

- 继承 / 复用 RAG-Anything 的大量底层能力：多模态解析、多模态处理器、LightRAG 图检索、批处理、缓存、回调 / 重试等。
- 保留原项目 `LICENSE` 与源码署名；本项目同样以 [MIT License](LICENSE) 发布。
- QueryNest 新增的 Query Analyzer / Query Rewrite / Hybrid Retrieval 编排 / Reranker 抽象 / Citation / Context Builder / Document Management / Evaluation / CLI / FastAPI / Web 控制台等模块为 QueryNest 原创实现。

**变更明细见 [QUERYNEST_CHANGELOG.md](QUERYNEST_CHANGELOG.md)，重构分析见 [REFACTOR_ANALYSIS.md](REFACTOR_ANALYSIS.md)。**