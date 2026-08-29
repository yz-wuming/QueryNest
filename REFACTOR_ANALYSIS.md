# REFACTOR_ANALYSIS — RAG-Anything → QueryNest

> 面向复杂文档的多模态 Retrieval-Augmented Generation（RAG）系统
>
> 本文件是「产品化二次开发」第一阶段的完整分析产物，用于指导后续所有重构决策。核心原则：**保留底层成熟能力，重新设计公共 API 与编排层，新增 QueryNest 独有的产品级能力**。

---

## 1. 项目概览与规模

- 核心包：`raganything/`，共 20 个 Python 文件
- 核心代码规模（约）：
  - `parser.py` 2468 行（解析器 + 3 个后端）
  - `processor.py` 1973 行（主流水线编排）
  - `modalprocessors.py` 1376 行（多模态处理器）
  - `query.py` 762 行（查询）
  - `batch_parser.py` 588 行、`batch.py` 389 行
  - `omml_extractor.py` 645 行、`enhanced_markdown.py` 442 行
  - 其余支撑模块 ~1700 行
- 示例：`examples/` 13 个；测试：`tests/` 31 个文件；文档：`docs/` 6 篇
- 第三方依赖：`lightrag-hku`、`mineru[core]`、`huggingface_hub`、`tqdm`；可选：PaddleOCR、WeasyPrint 等
- 底层模型：外部函数注入（LLM/VLM/Embedding），支持 OpenAI / Ollama / LM Studio / MiniMax / vLLM 等

---

## 2. 每个文件的职责

| 文件 | 职责 | 类别 |
|---|---|---|
| `__init__.py` | 包入口；导出 `RAGAnything`、`RAGAnythingConfig`、`Parser` 及若干可选模块（try/except 保护） | 入口 |
| `base.py` | `DocStatus` 枚举（ready/handling/…/failed） | 基础 |
| `config.py` | `RAGAnythingConfig` dataclass，含环境变量读取与回退兼容 | 配置 |
| `raganything.py` | `RAGAnything` 主类（组合 Query/Processor/Batch 三个 mixin）；LightRAG 生命周期、parse_cache/multimodal_status_cache 初始化、模态处理器装配、存储 finalize | **核心编排** |
| `processor.py` | `ProcessorMixin`：`parse_document`、`process_document_complete`、`insert_content_list`、多模态内容编排、doc_status 管理、缓存键生成 | **核心编排** |
| `query.py` | `QueryMixin`：文本查询 `aquery`、多模态查询 `aquery_with_multimodal`、VLM 增强查询 `aquery_vlm_enhanced`、多模态查询缓存 | **核心查询** |
| `batch.py` | `BatchMixin`：`process_folder_complete`（旧）、`process_documents_batch`（新，委托 BatchParser） | 批处理集成 |
| `parser.py` | `Parser` 基类 + `MineruParser` / `DoclingParser` / `PaddleOCRParser` + `register_parser` 注册机制 + CLI | **解析核心** |
| `batch_parser.py` | `BatchParser` 独立批处理 + `BatchProcessingResult` + 增量 manifest（size/mtime/md5） | 批处理 |
| `modalprocessors.py` | `BaseModalProcessor` + Image/Table/Equation/Generic 处理器 + `ContextExtractor` / `ContextConfig` 上下文注入 | **多模态核心** |
| `prompt.py` | `PROMPTS` 全局提示词注册表（20+ 模板，含带/不带上下文两套） | 提示词 |
| `prompts_zh.py` | `PROMPTS_ZH` 中文提示词（与 PROMPTS 1:1） | 提示词 |
| `prompt_manager.py` | `set_prompt_language` 等语言切换（线程安全快照替换） | 提示词 |
| `callbacks.py` | `ProcessingCallback` / `MetricsCallback` / `CallbackManager` 事件系统 | 可观测性 |
| `resilience.py` | `retry` / `async_retry` / `CircuitBreaker`（未接入核心流水线） | 可观测性 |
| `utils.py` | 工具：`separate_content`、`insert_text_content`、`get_processor_for_type`、图片 base64、表格/公式解析辅助、章节路径 | 工具 |
| `asset_urls.py` | 本地路径 → 公共 media URL 映射（`*_public_url`） | 工具 |
| `omml_extractor.py` | DOCX 中 OMML 公式 → LaTeX 提取，回填 content_list | 多模态支撑 |
| `enhanced_markdown.py` | Markdown → PDF 转换工具（WeasyPrint/Pandoc/ReportLab 后端） | 工具 |

---

## 3. 依赖图

```
                        ┌─────────────────────────────┐
                        │   RAGAnything (主类)        │
                        │   raganything.py            │
                        └─────────────┬───────────────┘
                                      │ 组合
        ┌──────────────┬──────────────┼──────────────┬──────────────────┐
        ▼              ▼              ▼              ▼                  ▼
  ProcessorMixin   QueryMixin   BatchMixin   (初始化)         RAGAnythingConfig
  processor.py     query.py     batch.py                    config.py
        │              │              │
        ▼              │              ▼
  modalprocessors──┐   │        batch_parser.py
        │          │   │              │
        ▼          │   ▼              ▼
  BaseModalProc    │  LightRAG     Parser(get_parser)
  Image/Table/     │  (第三方)     parser.py
  Equation/Generic │               ├─ MineruParser(子进程 mineru)
        │          │               ├─ DoclingParser(API)
        │          │               └─ PaddleOCRParser(OCR)
        │          │                      │
        ▼          │                      ▼
  utils.py  ◄──────┘            asset_urls.attach_public_media_urls
  prompt.py / prompts_zh.py /                  │
  prompt_manager.py ◄──────────────────────────┘
  callbacks.py (事件)   resilience.py (可选)
```

- 强核心：`raganything.py` → `processor.py` → `modalprocessors.py` / `utils.py` / `prompt.py` / `config.py`
- 查询侧：`query.py` → `LightRAG.aquery`；多模态查询复用 `modal_processors`
- 批处理侧：`raganything.py` → `batch.py` → `batch_parser.py` → `parser.py`

---

## 4. 数据流（文档进入系统）

```
文件(PDF/Office/图片/文本)
   │  ① parse_document → Parser 分发（MinerU 子进程 / Docling / PaddleOCR）
   ▼
content_list (结构化内容块：text / image / table / equation / custom)
   │  ② separate_content
   ├─▶ text_content ──▶ insert_text_content ──▶ LightRAG（纯文本块入库、分块）
   │
   └─▶ multimodal_items
          │  ③ 每个项经对应 ModalProcessor（Image 用 VLM，Table/Equation/Generic 用 LLM）
          ▼
      enhanced_caption + entity_info
          │  ④ _create_entity_and_chunk
          ▼
      text_chunks_db + chunks_vdb + entities_vdb + full_entities
          │  ⑤ extract_entities / merge_nodes_and_edges（LightRAG 图构建）
          ▼
   update doc_status（doc_status_storage）
       附：parse_cache（KV，按文件内容哈希缓存解析结果）
            multimodal_status_cache（KV，多模态完成状态）
```

## 5. 数据流（用户查询）

```
用户问题
   │  aquery（纯文本）
   ▼
LightRAG.aquery（mode=local/global/hybrid/mix/naive）
   │  → 向量召回(Dense) + 图召回(Graph) 内建于 LightRAG
   ▼
检索上下文 → LLM → 返回答案

用户问题 + 多模态内容（图片/表格/公式）
   │  aquery_with_multimodal → _process_multimodal_query_content
   ▼
各内容项走处理器 → 描述文本拼接 → 增强 query → aquery

用户问题（带 vision_model_func，VLM 增强路径）
   │  aquery_vlm_enhanced
   ▼
LightRAG only_need_prompt=true（只取检索 prompt，不含最终答案）
   │  正则提取 Image Path → 校验安全目录 → base64
   ▼
构建 VLM messages（[VLM_IMAGE_N] 标记定位图片）→ vision_model_func → 答案
```

---

## 6. 现有能力盘点

**继承 / 复用（成熟、稳定，直接保留或轻封）：**

1. MinerU / Docling / PaddleOCR 解析后端 + 自定义解析器注册机制
2. 多模态处理器（Image/Table/Equation/Generic）+ 上下文注入（ContextExtractor）
3. LightRAG 深度集成（向量 + 图 + KV 存储、实体抽取、图合并）
4. 批处理（BatchParser 线程池 + 增量 manifest）
5. 三层缓存（parse_cache / multimodal_status_cache / llm_response_cache + 多模态查询缓存）
6. 多语言提示词（en/zh 切换）
7. 回调事件系统 + 重试/熔断工具
8. 路径安全校验（防 prompt injection 读取任意文件）、公共 media URL
9. DOCX 公式（OMML→LaTeX）提取

**缺失（QueryNest 需新增）或薄弱（需强化）：**

| 能力 | 现状 | 结论 |
|---|---|---|
| Query Analyzer | 无 | **新增** |
| Query Rewrite | 无 | **新增** |
| Hybrid Retrieval 编排 | 仅 LightRAG 内建 dense+graph，无 BM25/keyword 融合编排、无候选融合/去重层 | **强化封装** |
| Reranker | 无独立实现（仅 reproduce/query.py 借用 lightrag.rerank.cohere_rerank） | **新增抽象 + 实现** |
| Citation 系统 | 无（仅 file_paths 透传） | **新增** |
| Multimodal Context Builder | ContextExtractor 存在，但无统一的 ContextItem 结构化产出 | **强化** |
| RAG Evaluation | 无检索指标（Recall/MRR/Precision），仅有 reproduce 的 LLM 评估脚本 | **新增** |
| Document Metadata | 无序的 DocStatus，无统一 DocumentMetadata | **新增** |
| Document 管理（list/get/delete/exists/status） | 无公共 API | **新增** |
| CLI | 仅有散落的 main() | **新增统一 CLI** |
| FastAPI 服务 | 无 | **新增** |

---

## 7. 冗余 / 可迁移 / 需重构

**冗余（不宜夸大）：**
- `batch.py` 的 `process_folder_complete`（旧 asyncio 实现）与 `process_documents_batch`（新）功能重叠 → 保留后者为首选，旧方法标 deprecated 或收敛
- `parse_cache` 与 `multimodal_status_cache` 初始化逻辑在 `raganything.py` 中「预置 LightRAG」与「自建 LightRAG」两条路径重复 → 提取公共方法

**Demo / Example（迁移或保留 reference）：**
- `examples/` 13 个文件 → 迁移为 `examples/querynest_*.py`，更新导入与类名
- `reproduce/`（index.py / query.py / llm_answer_evaluator.py）→ 属于研究复现脚本，作为参考保留，不并入主包

**需重构（核心）：**
- 包名/类名/配置类/日志名/环境变量前缀/cache-key 前缀/目录命名 全面品牌化为 QueryNest
- 数据模型：引入统一 `DocumentMetadata` + 结构化检索结果（含 source metadata）
- 查询侧：引入 Query Analyzer / Rewrite / Citation / Reranker / 统一 Context 结构

---

## 8. 回答专项问题

| # | 问题 | 结论 |
|---|---|---|
| 8 | 调用链 | 见 §3 依赖图；文档流程见 §4；查询见 §5 |
| 11 | LightRAG 作用 | 底层图-RAG 引擎：向量存储、图存储、KV 存储、文档分块、实体/关系抽取与图合并、图+向量混合检索、LLM 响应缓存、doc_status 管理。QueryNest 在其上封装**不会重复实现**数据库层 |
| 12 | MinerU 作用 | 默认文档解析后端（PDF/图片为主），通过子进程输出 `_content_list.json` + markdown，转为 text/image/table/equation 内容块 |
| 13 | 图片/表格/公式 | 图片：ImageModalProcessor 用 VLM（vision_model_func）看 base64 图片生成描述；表格：TableModalProcessor 用 LLM 从 markdown table_body 生成；公式：EquationModalProcessor 用 LLM 从 LaTeX 生成；均产出增强描述 + 实体，随后建 chunk + 图实体 |
| 14 | 缓存 | 解析缓存（parse_cache，内容哈希键）+ 多模态状态缓存 + LightRAG llm_response_cache + 自定义多模态查询缓存（md5 规范化 query/content/options） |
| 15 | 批处理 | BatchParser：ThreadPoolExecutor 并发、每文件超时、增量 manifest（size+mtime+md5 签名）跳过未变更文件、dry-run |
| 16 | 错误处理 | 部分采用 {success,error} 返回 + logger.error；存在零散 `except: pass`；resilience 模块未接入核心。**QueryNest 需统一异常体系** |
| 17 | 真 Hybrid Retrieval？ | 尚无。现存仅是 LightRAG 内建 dense+graph 混合。无 BM25/keyword、无多路融合/去重/重排编排 → QueryNest 需实现编排层 |
| 18 | 真 Reranker？ | 无。仅在研究脚本借用 cohere_rerank。QueryNest 需独立 `BaseReranker` + 实现 |
| 19 | 真 Citation？ | 无。查询结果无结构化来源信息。QueryNest 需新增 |
| 20 | Evaluation？ | 无检索指标；仅有 LLM 评估脚本。QueryNest 需 Recall@K / MRR / Precision@K 等 |

---

## 9. 重构计划（分阶段）

- **Phase 3 品牌/包重构**：`raganything/` → `querynest/`，`RAGAnything` → `QueryNest`（新公共 API `from querynest import QueryNest`），`RAGAnythingConfig` → `QueryNestConfig`；环境变量 `RAGANYTHING_*/PARSE_*` → `QUERYNEST_*`；日志名、cache key、目录（`rag_storage` → `querynest_storage`，含迁移提示而非强行改用户数据）、pyproject/setup/README/examples/tests
- **Phase 4 目录分层**：按 core/ingestion/multimodal/retrieval/query/evaluation/storage/api/utils 组织，遵循实际依赖，不机械套模板
- **Phase 5 新能力**：Query Analyzer、Query Rewrite、Hybrid Retrieval 编排、Reranker、Citation、Context Builder、Document Metadata、Document 管理、Evaluation
- **Phase 6 CLI + FastAPI + 配置系统**：`querynest ingest/query/documents/evaluate/serve`；FastAPI `/documents`、`/query`、`/query/multimodal`、`/health`；统一 `QUERYNEST_` 配置 + `.env`
- **Phase 7 异常 + 日志 + 测试**：`DocumentParseError/RetrievalError/RerankError/QueryError/CitationError`；日志名 `querynest`；重组 tests 并运行 pytest
- **Phase 8 README/Docs** 重写
- **Phase 9 QUERYNEST_CHANGELOG.md + 最终审查**

---

## 10. 目标 QueryNest 架构

```
querynest/
├── __init__.py                # 公共 API：QueryNest, QueryNestConfig, ...（保留 LegacyAdapter 可选）
├── core/
│   ├── engine.py              # QueryNest 主类（原 RAGAnything 编排）
│   ├── config.py              # QueryNestConfig（QUERYNEST_* 环境变量）
│   ├── models.py              # DocumentMetadata / ContextItem / SourceRef / 检索结果 dataclass
│   └── exceptions.py          # DocumentParseError / RetrievalError / ... 统一异常
├── ingestion/
│   ├── pipeline.py            # 主流水线（原 ProcessorMixin）
│   ├── parser.py              # 原 parser.py（保留 3 后端 + 注册机制）
│   ├── chunker.py             # 文本/多模态 chunk 构建
│   ├── metadata.py            # DocumentMetadata 组装
│   └── batch.py               # 原 batch_parser + BatchMixin
├── multimodal/
│   ├── processors.py          # 原 modalprocessors 的 4 个处理器 + Base + ContextExtractor
│   ├── formula.py             # 原 omml_extractor
│   └── utils.py               # 表格/公式/图片相关辅助
├── retrieval/
│   ├── hybrid.py              # 多路召回融合编排（dense/keyword/graph → 融合 → 去重）
│   ├── reranker.py            # BaseReranker + BGEReranker / 兼容实现
│   └── context.py             # Context Builder：ContextItem 结构化输出
├── query/
│   ├── analyzer.py            # Query Analyzer（TEXT/IMAGE/TABLE/EQUATION/MULTIMODAL/CROSS_DOCUMENT）
│   ├── rewrite.py             # Query Rewriter（多轮转完整问题）
│   └── citation.py            # Citation 系统（来源 metadata 归一）
├── evaluation/
│   ├── metrics.py             # Recall@K / MRR / Precision@K + 可选 Faithfulness 等
│   ├── dataset.py             # JSON 测试集加载
│   └── runner.py              # 运行评测 → evaluation/results.json
├── storage/
│   ├── cache.py               # 缓存封装（复用原三层缓存语义）
│   └── document_store.py      # Document CRUD（list/get/delete/exists/status）
├── api/
│   └── server.py              # FastAPI：/documents /query /query/multimodal /health
├── cli.py                     # querynest 命令行
└── utils/
    ├── logging.py             # querynest 日志
    └── files.py               # 图片 base64、路径安全等

tests/  test_config / test_ingestion / test_multimodal / test_retrieval /
        test_query / test_citation / test_documents / test_evaluation / test_api
```

> 目录以实际依赖为准动态收敛，不机械创建空目录；不重复实现 LightRAG 底层存储。

---

## 11. 明确继承 vs 新增（供 QUERYNEST_CHANGELOG 引用）

- **继承/复用**：解析后端（MinerU/Docling/PaddleOCR）、多模态处理器与上下文注入、LightRAG 集成、批处理与增量索引、三层缓存、多语言提示词、回调/重试工具、路径安全、DOCX 公式提取。（重构时保留其逻辑，仅改名 / 移动 / 轻封装；改动后回归测试）
- **QueryNest 新增**：Query Analyzer、Query Rewrite、Hybrid Retrieval 编排、Reranker 抽象与实现、Citation 系统、Multimodal Context Builder（ContextItem）、文档元数据模型、文档管理（list/get/delete/exists/status）、Evaluation（Recall@K/MRR/Precision@K）、统一 CLI、FastAPI API、`QUERYNEST_` 配置系统、统一异常/日志。

> 说明：只有真正完成代码后才能写入「已实现」清单，不虚构。