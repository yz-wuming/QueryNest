# QueryNest

**面向复杂文档的多模态 Document Intelligence & RAG 系统**

QueryNest 面向 PDF、Office 文档、图片、表格、公式等复杂文档场景，把文档解析、多模态内容处理、混合检索（Hybrid Retrieval）、Graph-RAG、重排（Reranking）、引用（Citation）与检索评测（Evaluation）整合到统一的 Retrieval-Augmented Generation（RAG）Pipeline 中。

传统 RAG 大多围绕纯文本设计，而真实文档往往同时包含正文、表格、图片、公式以及跨页结构。QueryNest 尝试把这些异构内容统一纳入检索与问答流程：文档进入后经过多模态解析与结构化索引，查询时通过混合检索召回多样内容，最终由 LLM/VLM 生成带可追踪来源的回答。


**技术标签**：`Python` · `FastAPI` · `LightRAG` · `MinerU` · `Docling` · `PaddleOCR` · `BM25` · `RRF` · `Reranker` · `VLM` · `RAG Evaluation`

---

## 核心能力

### 1. 多模态文档理解
对 PDF、Office 文档、图片、文本等做解析，并统一处理正文、表格、图片、公式与文档结构。底层接入实际存在的解析后端：`MinerU`（默认）与可选的 `Docling` / `PaddleOCR`（见 `querynest/ingestion/parser.py`）。

### 2. Hybrid Retrieval 混合检索
多路召回：**Dense（向量）** + **BM25（词法）** + **Graph（知识图）**，通过 **RRF（Reciprocal Rank Fusion）** 融合排序，再做去重与可选重排（见 `querynest/retrieval/hybrid.py`）。

### 3. Query Understanding 查询理解
`Query Analyzer` 判断问题意图：`TEXT / IMAGE / TABLE / EQUATION / MULTIMODAL / CROSS_DOCUMENT`；`Query Rewrite` 在多轮对话中做指代消解与问题改写（见 `querynest/query/`）。

### 4. Citation 引用
回答与检索结果建立结构化引用，例如 `[1] paper.pdf — Page 4; Table`。**没有可靠检索上下文时不生成虚构页码或引用**（见 `querynest/query/citation.py`）。

### 5. Document Management 文档管理
知识库文档的 `上传 / 列表 / 查询 / 删除 / 状态` 管理，支持单文件、批量与 ZIP 归档入库（见 `querynest/storage/document_store.py`、`querynest/api/server.py`）。

### 6. Model Registry & Provider 模型中心
多模型 Provider 统一管理：模型**注册 / 启用 / 禁用 / 设默认 / Test Connection**。Provider Adapter 覆盖 OpenAI、DeepSeek、Qwen、智谱 GLM、DashScope 以及本地 Ollama（见 `querynest/core/providers.py`）。Anthropic / Gemini 为架构预留、尚未接入真实协议。

### 7. RAG Evaluation 评测
支持 `Recall@K`、`Precision@K`、`MRR`、`NDCG@K`，以及可选的 `Faithfulness` 与 `Answer Relevancy`（未注入判定器/嵌入时借用启发式并如实标记，见 `querynest/evaluation/`）。

### 8. API + CLI
同时提供 FastAPI REST API（含单文件 Web 前端）、`querynest` 命令行与 Python API。

---

## 典型使用场景

### 学术论文问答
上传论文 PDF，提问“论文中哪个模型在表格里表现最好？”。系统结合正文、表格与页码进行检索，并返回结构化引用。

### 企业知识库
将多份 PDF / Office 文档入库为知识库，通过自然语言跨文档查询信息。

### 技术文档问答
针对包含代码、表格、图片和公式的技术文档做检索式问答。

### 多模态文档分析
面向图片、表格、公式等非纯文本内容提问，由 Query Analyzer 识别意图并选择相应检索与生成路径。

---

## Architecture

查询链路遵循“分析 → 改写 → 多路召回（Dense + BM25 + Graph）→ RRF 融合 → 去重 → 可选重排 → 构造上下文 → LLM/VLM 生成 → 引用 → 输出 Answer + Sources”的编排，叠加会话持久化与查询 Trace（可观测）能力。

---

## 技术实现

### Hybrid Retrieval
真实的 Dense / BM25 / Graph 多路召回。单独一路往往丢召回（如纯向量对术语/关键词不敏感，纯词法对语义难把握），多路互补后再融合。

### RRF Fusion
不同检索器的结果按 `RRF`（默认 `RRF_K=60`）统一排序，避免因各检索器打分尺度不同而偏袒某一路。

### Query Analyzer
默认基于确定性规则引擎（正则 + 关键词计分）判断问题类型并选择检索策略，可选注入 LLM 兜底做更细的语义分类，保证可离线、可测试。

### Citation
`CitationBuilder` 把生成回答中的来源映射回真实检索命中（文档 + 页码/块/类型）；检索无上下文时不伪造引用。

### Model Provider
`ProviderAdapter` 抽象屏蔽不同模型服务的协议差异，仅需 `base_url + model + api_key` 即可接入任意 OpenAI 兼容服务与 Ollama，上层无需感知各厂商差异。

---

## Demo / Screenshots

暂无可用截图或演示 Gif。后续补充文档上传、问答与 Citation、文档管理、模型管理等界面演示。

---

## Requirements

- Python **3.10+**
- 底层运行时依赖：`lightrag-hku<1.5`、`mineru[core]>=3.4.1`（见 `pyproject.toml`）
- LLM / Embedding 通过 OpenAI 兼容 API 或 Ollama 注入；VLM / Reranker 可选
- Docker（可选，容器部署）

---

## Installation

```bash
pip install -e .            # 完整安装（含 MinerU 等解析器）
pip install -e ".[api]"     # 仅核心 + API/CLI（不含重型解析器）
pip install -e ".[all]"     # 全部可选依赖
```

开发/测试：`pip install -e ".[dev]"`。

> 说明：Query Analyzer / Rewrite / Hybrid 编排 / BM25 / Citation / Evaluation 等为纯 Python，可在缺少数重型依赖时独立测试；PDF 多模态解析依赖 MinerU（需单独安装并可能首次下载模型）。

---

## Configuration

复制并填写环境变量：

```bash
cp .env.example .env
```

核心变量（默认值均来自 `querynest/core/config.py`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QUERYNEST_LLM_API_KEY` | 空 | LLM API Key（问答必填） |
| `QUERYNEST_LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 接口地址 |
| `QUERYNEST_LLM_MODEL` | `gpt-4o` | LLM 模型名 |
| `QUERYNEST_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding 模型名 |
| `QUERYNEST_EMBEDDING_DIM` | `1024` | 向量维度（入库时写入，应与 embedding 模型一致） |
| `QUERYNEST_VLM_*` | `gpt-4o` | 可选，视觉/VLM 问答 |
| `QUERYNEST_ENABLE_RERANK` | `false` | 可选重排开关 |
| `QUERYNEST_PARSER` | `mineru` | 解析器：`mineru / docling / paddleocr` |
| `QUERYNEST_STORAGE_DIR` | `./querynest_storage` | 存储目录 |
| `QUERYNEST_API_HOST` / `QUERYNEST_API_PORT` | `0.0.0.0` / `9621` | 服务监听地址与端口 |

完整配置见 `.env.example` 与 `querynest/core/config.py`。

---

## Quick Start

```bash
pip install -e .[api]
cp .env.example .env        # 填好 LLM / Embedding 配置
```

命令行演示（纯文本样例，使用随仓库提供的 `examples/data/sample.txt`）：

```bash
python examples/quickstart.py examples/data/sample.txt
```

Python 方式：

```python
import asyncio
from querynest import QueryNest, QueryNestConfig
from querynest.core.clients import build_openai_llm_func, build_openai_embedding_func

async def main():
    config = QueryNestConfig()
    engine = QueryNest(
        config,
        llm_model_func=build_openai_llm_func(config),
        embedding_func=build_openai_embedding_func(config),
    )
    await engine.ingest("paper.pdf")
    result = await engine.query("这个表格中哪个模型效果最好？")
    print(result.answer)
    for src in result.sources:
        print(src.display())          # 例如 "[1] paper.pdf — Page 4; Table"

asyncio.run(main())
```

---

## CLI

```bash
querynest --version                          # 查看版本
querynest ingest document.pdf               # 解析并入库文档
querynest query "这个表格中哪个模型效果最好？"   # 发起查询
querynest documents list                    # 列出文档
querynest documents delete <id>             # 删除文档
querynest evaluate evaluation/datasets/sample.json   # 运行评测
querynest serve                             # 启动 API 服务
```

---

## API

本地启动：

```bash
querynest serve
# 或
python -m uvicorn querynest.api.server:create_app --factory --host 0.0.0.0 --port 9621
```

核心端点：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（含 `engine_ready`） |
| POST | `/documents` / `/documents/upload` | 文档入库 / 文件上传 |
| GET/DELETE | `/documents`、`/documents/{id}` | 文档列表 / 删除 |
| POST | `/query`、`/query/multimodal` | 问答 / 多模态问答 |
| GET | `/conversations` | 会话列表 |
| GET | `/models` | 模型列表与生效模型 |
| GET | `/api/traces` | 查询轨迹（可观测，不含 Secret） |
| GET | `/openapi.json` | OpenAPI 文档 |

启动服务后可通过 **`http://localhost:9621/docs`** 查看完整的 Swagger / OpenAPI 交互文档，并可通过 `GET /` 访问内置的 Web 前端。

---

## Docker

存在 `Dockerfile`（后端 + 单文件前端，容器内监听 8080）与 `docker-compose.yml`（宿主 `9621 : 容器 8080`）。

```bash
docker compose up --build
# 后台运行：docker compose up -d --build
```

- 访问：`http://localhost:9621`
- 持久化：宿主 `./querynest_storage` 挂载到容器 `/data/querynest_storage`
- 环境变量：先在 `.env` 配置后注入容器

**验证状态**：Docker 配置已提供，但当前开发环境未执行实际的 build/run 验证，请在具备 Docker 的环境先行验证后使用。

---

## Evaluation

评测数据集（JSON / JSONL）位于 `evaluation/datasets/`（当前含 `example.json`、`sample.json`）。

```bash
querynest evaluate evaluation/datasets/sample.json
# 或
python scripts/run_evaluation.py --list-datasets
```

支持的指标：`Recall@K`、`Precision@K`、`MRR`、`NDCG@K`，以及可选的 `Faithfulness`（需注入判定器/嵌入；未注入时用词面启发式并如实标记）与 `Answer Relevancy`。

离线模式用于验证 Evaluation Pipeline 本身；真实检索评测需接入实际 Retriever、Embedding 与 LLM（见 `querynest/evaluation/runner.py`）。

---

## Testing

当前本机实际验证（Python 3.10）：

- **pytest**：`208 passed / 0 failed`
- FastAPI health endpoint：PASS
- OpenAPI / Document endpoint：PASS
- Frontend 静态托管：PASS

**未在当前环境验证**：真实 VLM 链路、真实 Reranker、Docker runtime。

---

## 项目结构

```
querynest/
├── core/          # 引擎门面、配置、模型注册表、Provider Adapter、Trace
├── ingestion/     # 文档解析（MinerU / Docling / OCR / lite）与批处理
├── multimodal/    # 表格、公式、增强 Markdown 等多模态处理
├── retrieval/     # 混合检索（Dense+BM25+Graph）、RRF、重排、上下文
├── query/         # 查询分析、改写、引用、查询编排
├── evaluation/    # RAG 评测：指标、数据集、Runner
├── storage/       # 文档、会话、缓存持久化
├── api/           # FastAPI 服务 + 单文件前端
└── cli.py         # querynest 命令行入口
```

依赖方向：`core` 在最底层；`ingestion / retrieval / query / multimodal` 仅依赖 `core` 与 `storage`；`api / evaluation / cli` 在其上。前端为单文件 `querynest/api/static/index.html`（原生 HTML/CSS/JS，无框架）。

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 分层架构与一次问答的完整数据流
- [`docs/ENGINEERING.md`](docs/ENGINEERING.md) — 工程指南（Supported / Available / Not tested）
- [`docs/QUERYNEST_CHANGELOG.md`](docs/QUERYNEST_CHANGELOG.md) — 相对 RAG-Anything 的变更与新增能力
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — 演示脚本
- [`docs/TEST_DATA_REQUIREMENTS.md`](docs/TEST_DATA_REQUIREMENTS.md) — 评测数据要求

---

## Limitations

当前版本存在以下边界：

- 图片 VLM 链路依赖外部视觉模型 Key
- Reranker 为可选组件（默认 Noop）
- Docker runtime 尚未在当前开发环境实测
- 部分 Evaluation 指标需外部 Judge 或实际 Retriever 才可获得完整结果

---

## License / Attribution

QueryNest 基于 [HKUDS RAG-Anything](https://github.com/HKUDS/RAG-Anything)（Copyright (c) HKUDS，MIT License）进行二次开发，保留原项目 `LICENSE` 与版权声明；本项目同样以 [MIT License](LICENSE) 发布。

在原项目底层能力（多模态解析、LightRAG 图检索、批处理、缓存、回调/重试）之上，QueryNest 新增 Query Analysis / Query Rewrite / Hybrid Retrieval 编排 / BM25 / Reranker 抽象 / Citation / Context Builder / Document Management / RAG Evaluation / Model Registry / Provider Adapter / Trace / CLI / FastAPI 等工程实现。
