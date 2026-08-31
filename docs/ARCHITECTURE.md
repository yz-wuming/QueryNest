# QueryNest Architecture

QueryNest 是一个端到端的多模态 **Retrieval-Augmented Generation（RAG）** 系统。
本文档描述分层架构、核心模块职责，以及一次问答请求的完整数据流。

> 术语约定：`RAG` = Retrieval-Augmented Generation；`VLM` = Vision-Language Model；
> `LLM` = Large Language Model。

---

## 1. 分层总览

```
┌────────────────────────────────────────────────────────────┐
│  Frontend  (querynest/api/static/index.html, 单页应用)      │
│  Chat · Documents · Models · Evaluation · API · Settings    │
└───────────────────────────────┬────────────────────────────┘
                                │ HTTP / JSON
┌───────────────────────────────▼────────────────────────────┐
│  API Layer  (querynest/api/server.py, FastAPI)              │
│  /documents /query /conversations /models /evaluation        │
└───────────────────────────────┬────────────────────────────┘
┌───────────────────────────────▼────────────────────────────┐
│  Application & Query Layer                                  │
│  query/base.py(QueryEngine) · query/analyzer.py             │
│  query/rewrite.py · query/citation.py                       │
├─────────────────────────────────────────────────────────────┤
│  Retrieval Layer                                            │
│  retrieval/hybrid.py · retrieval/keyword.py                 │
│  retrieval/reranker.py · retrieval/context.py               │
├─────────────────────────────────────────────────────────────┤
│  Ingestion Layer                                            │
│  ingestion/parser.py · ingestion/lite.py                    │
│  ingestion/processor.py · ingestion/batch.py                │
├─────────────────────────────────────────────────────────────┤
│  Multimodal Layer                                           │
│  multimodal/processors.py · enhanced_markdown.py            │
│  multimodal/omml_extractor.py                                │
├─────────────────────────────────────────────────────────────┤
│  Model  Registry & Provider Adapters                        │
│  core/model_registry.py · core/providers.py · core/clients.py│
│  core/secrets.py                                             │
├─────────────────────────────────────────────────────────────┤
│  Storage (querynest/storage/)                               │
│  document_store.py · conversation_store.py · cache.py       │
│  + 向量/全文索引 (querynest_storage)                        │
├─────────────────────────────────────────────────────────────┤
│  Cross-cutting: core/engine.py · core/config.py             │
│  core/trace.py(观测) · core/exceptions.py · core/logging.py │
│  evaluation/ (runner · metrics · dataset · ablation)         │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 各层职责

### Frontend（`querynest/api/static/index.html`）
单页应用，左侧 Sidebar + 右侧主工作区。桌面优先、响应式。负责：
- Chat：会话、发送 / 停止、历史加载、重命名 / 删除、会话隔离、答案复制、引用展示。
- Documents：单文档 / 批量上传、ZIP 批量入库、状态（Queued/Processing/Completed/Failed）、删除。
- Models：模型注册 / 增删改 / 启停 / 设默认 / Test Connection，密钥仅回显掩码。
- Evaluation：触发真实评估并展示 Recall/Precision/MRR/NDCG。
- API：端点文档 + 可直接运行的 cURL 示例 + 一键复制。
- Settings：检索模式、top_k、provider 配置保存 / 重置。

### API Layer（`querynest/api/server.py`，FastAPI）
统一 REST 入口、请求/响应模型、引擎依赖注入、ZIP/Batch 安全防护、统一错误码。

### Query Layer（`querynest/query/`）
- `base.py`：`QueryEngine`，编排一次查询的完整流程，聚合检索 + 生成。
- `analyzer.py`：Query Analyzer —— 判断查询意图（表/多模态/纯文本），决定是否走多模态管线。
- `rewrite.py`：Query Rewrite —— 会话历史下的查询改写 / 补充。
- `citation.py`：Citation —— 把生成答案中的来源映射回真实检索命中文档。

### Retrieval Layer（`querynest/retrieval/`）
- `hybrid.py`：Hybrid Retriever —— 融合向量检索与词法检索（BM25 类）的混合检索。
- `keyword.py`：Keyword —— 词法检索实现。
- `reranker.py`：Reranker —— 对候选片段做相关性重排，提升召回精度。
- `context.py`：Context Builder —— 把命中的多模态片段（文本/表/图/公式）组装为生成上下文。

### Ingestion Layer（`querynest/ingestion/`）
- `parser.py`：多格式文档解析（PDF/Office/图片/文本），按扩展名路由到对应解析器。
- `lite.py`：轻量文本解析（TXT/MD/JSON），无重型依赖。
- `processor.py`：文档入库管线 —— 解析 → 分块 → 嵌入 → 建索引 → 文档登记。
- `batch.py` / `batch_parser.py`：批量 / ZIP 批量入库容器，逐个文件独立处理并报告状态。

### Multimodal Layer（`querynest/multimodal/`）
- `processors.py`：多模态内容处理器（OCR / 表格 / 公式 / 图片理解）。
- `enhanced_markdown.py`：把多模态解析产物转成增强 Markdown（保留表 / 图 / 公式结构）。
- `omml_extractor.py`：Office MathML（公式）抽取。

### Model Registry & Provider Adapters（`querynest/core/`）
- `model_registry.py`：模型注册表 —— Add/Edit/Delete/Enable/Disable/Default/Test Connection，禁用模型不可被 Chat 使用。
- `providers.py`：统一 Provider 适配接口：
  - `test_connection()`
  - `generate()`
  - `supports_vision()`
  - `supports_embeddings()`
  覆盖 OpenAI / DeepSeek / Qwen / GLM / DashScope / OpenAI-Compat / Ollama。
- `clients.py`：底层 client 封装。
- `secrets.py`：API Key 安全存取（只写不读明文、脱敏）。

### Storage（`querynest/storage/`）
- `document_store.py`：文档元数据仓库。
- `conversation_store.py`：会话持久化 —— 消息保存 `model_id / sources / trace_id`，隔离会话。
- `cache.py`：解析 / 检索缓存（避免重复解析与重复嵌入）。
- 索引数据存 `querynest_storage/`（不入 Git）。

### Cross-cutting
- `core/engine.py`：核心引擎，聚合各层能力。
- `core/config.py`：配置与支持的扩展名。
- `core/trace.py`：Query Trace，链路观测（每一次查询的可追踪记录）。
- `core/exceptions.py`：统一异常体系。
- `evaluation/`：真实评测 —— `metrics.py` 计算 Recall/Precision/MRR/NDCG/Faithfulness，
  `runner.py` 用真实检索 + 真实生成驱动，`dataset.py` 读取 golden 数据集，`ablation.py` 做消融。

---

## 3. 一次问答的完整数据流

```
User
 │ 在 Chat 输入问题并发送
 ▼
Frontend (index.html)────────────── POST /query  {query, mode, top_k, history, model_id}
 ▼
API Layer (server.py)────────────── 校验 + 注入引擎
 ▼
Query Engine (query/base.py)─────── orchestrates the whole pipeline
 ▼
Query Analyzer (query/analyzer.py) 判断意图：纯文本 / 表格 / 多模态？
 ▼
Query Rewrite (query/rewrite.py)  （有历史时）结合会话历史改写 / 补全提问
 ▼
Retriever (retrieval/hybrid.py     向量检索 + 词法检索 → 候选片段
          + keyword.py)
 ▼
Reranker (retrieval/reranker.py)   对候选重排，提升 precision
 ▼
Context Builder (retrieval/context.py) 抽取多模态片段，组装上下文
 ▼
LLM / VLM (providers.py 适配器)    用上下文 + 系统提示生成回答
 ▼
Citation (query/citation.py)       把回答中的来源映射回真实命中文档
 ▼
Conversation Store (conversation_store.py) 持久化 message(model_id, sources, trace_id)
 ▼
Trace (core/trace.py)              记录本次查询链路，供观测
 ▼
API Layer → Frontend              返回 {answer, sources, retrieval}
```

**并行管线（多模态文档入库）：**

```
ZIP / 批量文件
 ▼
安全解压 (zip slip / 压缩炸弹防护)
 ▼
File discovery → Format detection
 ▼
Parser 路由：PDF >> lite/TXT/MD/JSON · DOCX/XLSX/PPTX >> Office · PNG/JPG >> Multimodal
 ▼
Multimodal: OCR / 表格 / 公式 / 图片理解
 ▼
Chunking → Embedding → Index → Document Store
 ▼
Retrieval → Citation
```

---

## 4. 模块依赖方向

- 下层不依赖上层；上层依赖下层抽象。
- `querynest.core` 是最底层（engine/config/models/exceptions/trace）。
- `querynest.ingestion`、`querynest.retrieval`、`querynest.query`、`querynest.multimodal` 仅依赖 `core` 与 `storage`。
- `querynest.api` 依赖以上所有层，并注入 `querynest.core.engine`。
- `querynest.evaluation` 依赖 `retrieval` 与 `core`，可与 API 分离运行。
- `querynest/api/static/index.html` 是唯一前端入口，通过 REST 与 `querynest.api.server` 通信。

---

## 5. 设计要点与取舍

- **ZIP = Batch Ingestion Container**：ZIP 不是 RAG 文档，而是批量入库容器 —— 安全解压后识别内部支持格式，逐个进入既有 ingestion pipeline。
- **统一的 Provider Adapter**：新增 Provider 只需实现统一接口，无需复制整套调用逻辑。
- **可观测**：每次查询写入 `QueryTrace`，便于调试与评估对照。
- **真实评测**：Evaluation 基于 golden dataset + 真实检索 + 真实生成计算指标；无数据的指标明确标注 NOT AVAILABLE，不硬编码 0.0。
- **安全**：API Key 掩码、ZIP 解压安全、`.env` / `secrets.json` 不入 Git、文档存储与源码隔离。