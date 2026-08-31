# QueryNest — Change Log & 原项目能力 vs QueryNest 新增能力

`QueryNest 2.0.0` 是对 [RAG-Anything](https://github.com/HKUDS/RAG-Anything)（MIT License）的
**产品化二次开发**：品牌重构 + 分层架构 + 一批产品级新能力。本文档如实区分
**继承/复用的基础能力** 与 **QueryNest 新增实现**，不虚构任何未经代码实现的功能。

---

## 1. 变更概览

- **包名**：`raganything` → `querynest`
- **核心入口**：`RAGAnything` → `QueryNest` / `RAGAnythingConfig` → `QueryNestConfig`
- **环境变量前缀**：`QUERYNEST_`（兼容回退旧名称）
- **CLI 命令**：`querynest`
- **日志命名**：`querynest.*`
- **存储目录默认**：`./querynest_storage`
- **公开 API**：`from querynest import QueryNest; engine = QueryNest(config); await engine.ingest(...); await engine.query(...)`

---

## 2. A. Reused / Refactored capabilities（继承自 RAG-Anything）

| 底层能力 | 落点 | 状态 |
| --- | --- | --- |
| MinerU / Docling / PaddleOCR 解析 | `querynest/ingestion/parser.py` | 复用，路径化迁移 |
| 批处理 / 增量索引 | `querynest/ingestion/batch.py`, `batch_parser.py` | 复用 |
| 图片 / 表格 / 公式多模态处理器 | `querynest/multimodal/processors.py`, `enhanced_markdown.py` | 复用 |
| DOCX OMML 公式 → LaTeX 提取 | `querynest/multimodal/omml_extractor.py` | 复用（纯 stdlib） |
| LightRAG 图-RAG 接入 | `querynest/core/engine.py`（`_RAGAdapter`）+ `query/query/base.py` | 封装重排 |
| 三层缓存（parse_cache / multimodal_status 等） | `core/engine.py#_RAGAdapter` | 复用 |
| 多轮问答 / VLM 增强 / 多模态 QueryMixin | `querynest/query/base.py` | 复用（QueryNest 门面调用） |
| 回调 / 韧性（retry / CircuitBreaker）/ Prompt 多语言 | `querynest/callbacks.py`, `resilience.py`, `prompt_manager.py` 等 | 复用 |
| 旧 `ProcessorMixin`/`QueryMixin`/`BatchMixin` 编排 | 内部经 `_RAGAdapter` 组合，不对外暴露 | 封装重构 |

> 以上均为**依赖迁移 + 内部封装**，核心多模态处理逻辑保持不变。

---

## 3. B. QueryNest original enhancements（新增实现）

> 以下每一项都有对应源代码；纯新增、不来自原项目。

| 能力 | 模块 | 说明 |
| --- | --- | --- |
| 品牌 / 包名 / 公共 API 重构 | `querynest/__init__.py`, `core/engine.py`, `core/config.py` | 全新门面 `QueryNest`，内部组合继承能力 |
| 统一配置系统 | `core/config.py` | `QUERYNEST_*` 前缀 + 旧变量回退 |
| 统一数据模型 | `core/models.py` | `ContentType`/`ContextItem`/`Citation`/`DocumentMetadata`/`RetrievalResult` |
| 统一异常体系 | `core/exceptions.py` | `QueryNestError` 及 `DocumentParseError`/`RetrievalError`/`RerankError`/`QueryError`/`CitationError`/`EvaluationError` 等，不吞异常 |
| 统一日志 | `core/logging.py` | `querynest.*` 命名，替代旧 `raganything` 前缀 |
| Query Analyzer | `query/analyzer.py` | 规则引擎 + 可选 LLM 兜底，识别 TEXT/IMAGE/TABLE/EQUATION/MULTIMODAL/CROSS_DOCUMENT |
| Query Rewrite | `query/rewrite.py` | 多轮上下文指代消解，改写为自包含问题 |
| Hybrid Retrieval 编排 | `retrieval/hybrid.py` | Dense+Keyword+Graph 多路召回 → RRF/score 融合 → 去重 → 重排 |
| Keyword（BM25）检索器 | `retrieval/keyword.py` | 纯 Python BM25，第三条召回路（新增） |
| Reranker 抽象 + 实现 | `retrieval/reranker.py` | `BaseReranker` 抽象 + `BGEReranker`/`NoopReranker`，配置开关 |
| Citation 系统 | `query/citation.py` | 检索命中随带 source metadata，规整去重排序为 `[N]` 引用 |
| Multimodal Context Builder | `retrieval/context.py` | 文本/图片/表格/公式统一为结构化 `ContextItem` |
| Document Management | `storage/document_store.py` | `list/get/delete/exists/status/read_source` 知识库管理 |
| 缓存模块 | `storage/cache.py` | 轻量、可落盘 KV 缓存 |
| Evaluation 框架 | `evaluation/metrics.py`, `dataset.py`, `runner.py` | Recall@K/MRR/Precision@K/NDCG@K +（可选）Faithfulness/Answer Relevancy；JSON 测试集 → `results.json` |
| CLI | `querynest/cli.py`, `__main__.py` | `ingest/query/documents/evaluate/serve`，命令名 `querynest` |
| FastAPI 服务 | `api/server.py` | `/health`,`/documents`,`/query`,`/query/multimodal`，统一响应结构 |
| 测试体系 | `tests/` | `pytest tests/` 共 208 用例通过 |
| 文档 | `README.md`, `.env.example`, `pyproject.toml`, `setup.py` | 品牌化重写；旧 `REFACTOR_ANALYSIS.md` 与 `env.example` 已随发布移除 |

---

## 4. 新增能力设计要点（不重复造轮子的地方）

- **检索**：QueryNest 只负责「多路检索编排 + 融合 + 重排」，真正的数据检索（向量、图）仍由
  继承的 LightRAG 提供；BM25 为纯 Python 轻量实现。
- **解析/多模态**：完全继承原项目，未改动第三方（MinerU/LightRAG 等）源码，不重复实现底层数据库。
- **Citation/Evaluation**：源自检索命中本身携带的 `document_id/page/content_type/source`，
  依靠统一数据模型，而非在 Prompt 里要求模型编造。

---

## 5. 测试结果（本次重构后）

- 默认测试套件（`pytest tests/`）：**208 passed, 0 failed**（本次实测运行，Python 3.14.6 / Miniconda）。
- 覆盖：配置 / 文档解析注册表 / 多模态（公式提取）/ 检索（Hybrid/BM25/Reranker/Context）/
  Query（Analyzer/Rewrite）/ Citation / Document CRUD / Evaluation / API。
- 旧 `tests/legacy/`（依赖可选重型组件 MinerU/PaddleOCR/torch 与旧 `raganything` 模块路径）
  已随本次开源发布移除；当前测试体系仅含新 `tests/` 套件。

---

## 6. 技术栈

Parsing: MinerU / Docling / PaddleOCR · Graph-RAG: LightRAG · LLM/VLM & Embedding: OpenAI 兼容 /
Ollama 等（回调注入）· Rerank: FlagEmbedding / sentence-transformers（可选）· API: FastAPI + Pydantic ·
融合: RRF + BM25（纯 Python）· CLI: argparse · Logging: logging（`querynest.*`）

---

## 7. 目录保留与兼容说明（最终审查结论）

- 根目录保留旧 `raganything/` 包：经依赖分析，`examples/*`、`reproduce/*`、`tests/legacy/*` 仍
  以旧模块路径导入它；移除会破坏这些继承能力与旧测试。因此**有意保留**，不作为 QueryNest 公共
  API（新入口统一为 `querynest`），后续如需彻底下线可在此三项就绪后统一迁移。
- `asset_urls.py` 环境变量已同步为 `QUERYNEST_PUBLIC_ASSET_BASE_URL` /
  `QUERYNEST_PUBLIC_ASSET_STRIP_PREFIX` 优先、`RAGANYTHING_*` 回退，与配置系统前缀规则一致。

---

## 8. 已知问题 & 后续建议

- 完整 ingest/query 端到端依赖 `lightrag` 与 `mineru`（重型），本机未安装，相关路径以延迟导入 +
  守卫方式保证可测试性；生产部署需安装。
- 默认引擎未自动创建 LLM/Embedding 回调，需调用方注入 `llm_model_func`/`embedding_func` 或传入
  预初始化的 LightRAG 实例（见 `core/engine.py`）。
- Dense/Graph 命中依赖 LightRAG `only_need_prompt` 的文本封装（`_extract_hits_from_prompt`），
  结构化命中解析为尽力而为；若要精确 citation，建议后续适配 LightRAG 暴露原始命中的存储层接口。
- 后续可做：图/向量索引随文档删除的联动清理、增量更新、LLM 判定器集成、WebUI。