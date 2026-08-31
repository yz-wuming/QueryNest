# QueryNest

**QueryNest — Multimodal Document Intelligence & RAG**

QueryNest 是一个面向复杂文档的多模态 **Retrieval-Augmented Generation（RAG）** 系统，基于
[RAG-Anything](https://github.com/HKUDS/RAG-Anything)（MIT License）做产品化二次开发：分层架构、
`querynest` 包替换旧 `raganything` 包、以及一套可读、可测、可持续维护的 Query / Retrieval /
Citation / Evaluation / API / CLI / Document-Management 能力。目标是把「文档进入 → 多模态解析 →
结构化索引 → 混合检索 → 上下文构造 → LLM/VLM 生成 → 可引用回答」的整条链路逐层实现并讲清楚。

> 文档完整性说明：本 README 中的命令、接口、功能与测试结论均以**当前代码与当前实测结果**为准，
> 并如实区分「已实测 / 依赖外部环境的未实测项」，不虚构性能、架构或部署结果。

---

## 1. Features

| 能力 | 说明 |
| --- | --- |
| 多模态解析 | 继承 MinerU / Docling / PaddleOCR，解析 PDF / 图片 / Office 文档 |
| 多模态处理 | 表格结构化、公式（LaTeX / DOCX OMML）提取；图片理解走 VLM（需配置视觉模型） |
| Graph-RAG | 通过 LightRAG `global` 模式提供图检索（引擎在 `_build_graph_retriever` 中接线，依赖图索引可用） |
| Query Analyzer | 判断问题意图：TEXT / IMAGE / TABLE / EQUATION / MULTIMODAL / CROSS_DOCUMENT（确定性规则 + 可选 LLM 兜底） |
| Query Rewrite | 多轮上下文下的问题改写（指代消解） |
| Hybrid Retrieval | Dense（LightRAG `local`）+ Keyword（纯 Python BM25）+ Graph 多路召回，RRF 融合、去重、可选重排 |
| Reranker | 可插拔 `BaseReranker` 抽象 + `BGEReranker` / `NoopReranker`（默认 Noop，真实重排需配置模型） |
| Citation | 结构化引用 `[N] document — page/chunk/type`；检索无上下文时不产生引用、兜底不伪造页码 |
| Document Management | `list/get/delete/exists/status` 知识库管理（元数据 + 磁盘正文） |
| RAG Evaluation | Recall@K / Precision@K / MRR / NDCG@K + 可选 Faithfulness / Answer Relevancy |
| Model Registry & Provider | 模型 CRUD / 启停 / 默认 / Test Connection；多 Provider Adapter（OpenAI / DeepSeek / Qwen / GLM / DashScope / Ollama 等） |
| 会话持久化 | 会话与消息（含 model_id / sources / trace_id）持久化 |
| Query Trace | 每次查询链路可观测（`/api/traces`），不含任何 Secret |
| CLI + FastAPI | `querynest` 命令行 与 REST API |
| 统一配置 | `QUERYNEST_*` 环境变量前缀（兼容旧变量回退），支持 `.env` |

> 说明：项目**未**实现也未声称 Multi-Agent 协作；Graph-RAG 由 LightRAG 提供，仅在已建立图索引时可用，
> 未启用图检索时按代码设计优雅回退到 Dense + BM25，不会伪造图命中。

---

## 2. Architecture

```
querynest/
├── __init__.py / __main__.py   # 公开 API 出口 + python -m 入口
├── cli.py                      # CLI（ingest/query/documents/evaluate/serve）
├── core/                       # engine(门面)、config、models、exceptions、logging、
│                               # model_registry、providers、clients、secrets、trace
├── ingestion/                  # parser、lite、processor、batch、batch_parser
├── multimodal/                 # processors、enhanced_markdown、omml_extractor
├── retrieval/                  # hybrid、keyword(BM25)、reranker、context
├── query/                      # analyzer、rewrite、citation、base
├── evaluation/                 # metrics、dataset、runner、ablation
├── storage/                    # document_store、conversation_store、cache
└── api/                        # server.py(FastAPI) + static/index.html(单文件前端)
```

模块依赖方向：`core` 最底层；`ingestion / retrieval / query / multimodal` 仅依赖 `core` 与 `storage`；
`api` 依赖以上各层；`evaluation` 依赖 `retrieval` 与 `core`。前端为**单文件** `querynest/api/static/index.html`
（原生 HTML/CSS/JS，无框架），通过 REST 与后端通信。

一次查询数据流：

```
Query → Query Analyzer → Query Rewrite → [Dense + Keyword + Graph]
        → RRF Fusion → 去重 → (Rerank) → Context Builder
        → LLM/VLM → Citation → (Conversation Store + Trace) → 响应
```

---

## 3. Requirements

- Python **3.10+**（本项目在 3.10 上开发与测试）
- 底层运行时：`lightrag-hku<1.5`、`mineru[core]>=3.4.1`、`huggingface_hub`、`tqdm`
- LLM / Embedding 通过 OpenAI 兼容 API（或 Ollama 等）注入；VLM / Reranker 为可选
- Docker（可选，容器部署）

---

## 4. Installation

```bash
# 完整安装（含 MinerU 解析器等底层运行时）
pip install -e .

# 仅核心 + API/CLI（不含重型解析器时使用）
pip install -e ".[api]"

# 可选增强：OCR / 重排 / 文档转 PDF 等
pip install -e ".[paddleocr]"   # PaddleOCR
pip install -e ".[reranker]"    # BGE Reranker（FlagEmbedding）
pip install -e ".[all]"         # 全部可选依赖
```

开发/测试依赖：`pip install -e ".[dev]"`。

> 轻量路径说明：Query Analyzer / Rewrite / Hybrid 编排 / BM25 / Citation / Evaluation 等模块为纯 Python，
> 可在缺少数重型依赖时独立测试；PDF 多模态解析依赖 MinerU（需单独安装并可能首次下载模型）。

---

## 5. Configuration

配置统一使用 `QUERYNEST_` 前缀，未设置时回退读取旧变量名。复制并填写：

```bash
cp .env.example .env
```

核心变量（默认值来自 `querynest/core/config.py`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `QUERYNEST_LLM_API_KEY` | 空 | LLM API Key（必填，用于问答） |
| `QUERYNEST_LLM_BASE_URL` | `https://api.openai.com/v1` | LLM 接口地址 |
| `QUERYNEST_LLM_MODEL` | `gpt-4o` | LLM 模型名 |
| `QUERYNEST_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding 模型名 |
| `QUERYNEST_EMBEDDING_DIM` | `1024` | 向量维度（写入空间时使用，应与 embedding 模型一致） |
| `QUERYNEST_VLM_*` | 空 | 可选，图片/VLM 问答 |
| `QUERYNEST_RERANKER_MODEL` / `QUERYNEST_ENABLE_RERANK` | 空 / `false` | 可选重排 |
| `QUERYNEST_PARSER` | `mineru` | 解析器：mineru / docling / paddleocr |
| `QUERYNEST_STORAGE_DIR` | `./querynest_storage` | 存储目录 |
| `QUERYNEST_API_HOST` / `QUERYNEST_API_PORT` | `0.0.0.0` / `9621` | 服务监听地址与端口 |
| `QUERYNEST_QUERY_TOP_K` | `20` | 顶层检索命中数 |

---

## 6. Quick Start

```python
import asyncio
from querynest import QueryNest, QueryNestConfig


async def main():
    config = QueryNestConfig()          # 读取环境变量 / .env
    engine = QueryNest(
        config,
        llm_model_func=...,             # 注入 LLM 回调（OpenAI 兼容）
        embedding_func=...,             # 注入 Embedding 回调
    )
    await engine.ingest("paper.pdf")
    result = await engine.query("这个表格中哪个模型效果最好？")
    print(result.answer)
    for src in result.sources:
        print(src.display())            # 例如 "[1] paper.pdf — Page 4; Table"


asyncio.run(main())
```

也可以命令行演示（纯文本，使用随仓库提供的样例）：

```bash
# 先在 .env 中配置 LLM；然后
python examples/quickstart.py examples/data/sample.txt
```

---

## 7. CLI

```bash
querynest --version
querynest ingest document.pdf          # 解析并入库文档
querynest query "这个表格中哪个模型效果最好？"   # 发起查询
querynest documents list                # 列出文档
querynest documents get <id>            # 获取文档
querynest documents delete <id>         # 删除文档
querynest evaluate evaluation/datasets/sample.json   # 运行评测
querynest serve                          # 启动 API 服务
```

---

## 8. API

本地启动：

```bash
python -m uvicorn querynest.api.server:create_app --factory --host 0.0.0.0 --port 9621
```

（等价于 `querynest serve`，默认 `0.0.0.0:9621`。）

主要端点（当前 OpenAPI 共 24 个路径）：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查（含 `engine_ready`） |
| GET/POST/DELETE | `/documents` | 文档列表 / 添加 / 删除；另含 `/documents/upload`、`.../upload/batch`（ZIP 批量） |
| POST | `/query`、`/query/multimodal` | 查询 |
| GET/POST | `/conversations`、`/conversations/{id}/messages` | 会话与消息 |
| GET/POST | `/models` 及 `/models/{mid}/...` | 模型注册表 / 启用 / 默认 / 测试 |
| GET/POST | `/api/evaluation` | 评测读取与触发 |
| GET | `/api/traces`、`/api/traces/{id}` | 查询轨迹（可观测） |
| GET | `/openapi.json` | OpenAPI 文档 |

统一响应示例：

```json
{
  "answer": "...",
  "sources": [{"document": "paper.pdf", "page": 4, "type": "table", "score": 0.91}],
  "retrieval": {"num_hits": 10, "intent": "table"},
  "metadata": {}
}
```

---

## 9. Docker Deployment

> 状态说明：**本机未安装 Docker，以下文件已按项目真实 `Dockerfile` 编写，但尚未在本机实际 build/run 验证。**
> 请在有 Docker 的环境执行验证后再发布。

存在镜像构建文件 `Dockerfile`（单级、后端 + 单文件前端；容器内监听 **8080**；存储挂载在 `/data/querynest_storage`）
与编排文件 `docker-compose.yml`（单服务，宿主 **9621 → 容器 8080**）：

```bash
docker compose up --build
# 若需后台运行：docker compose up -d --build
```

- 心跳：`GET http://localhost:9621/health`
- 持久化：宿主 `./querynest_storage` 挂载到容器 `/data/querynest_storage`
- 密钥：先在 `.env` 配置，再挂载/注入给容器（参考 `.env.example`）

> 我（当前环境）无法在本机完成 `docker build` 与容器启动验证；因此**容器内实际运行结果未实证**，
> 只能提供与真实 `Dockerfile` 一致的标准用法，请勿据此文档视为已验证。

---

## 10. Evaluation

评测数据集（JSON / JSONL）放在 `evaluation/datasets/`（当前含 `example.json`、`sample.json`）。

```bash
querynest evaluate evaluation/datasets/sample.json
# 或
python scripts/run_evaluation.py --list-datasets
```

- 指标：`Recall@K`、`Precision@K`、`MRR`、`NDCG@K`；Faithfulness / Answer Relevancy 在未注入判定器时如实标记。
- 诚实说明：CLI 的 `evaluate` 默认使用离线空检索器（便于无引擎环境跑通流程），此时检索类指标为 **0**；
  真实检索评测需在代码中用真实 retriever 驱动（见 `querynest/evaluation/runner.py`）。

---

## 11. 测试与当前验证状态（以本机实测为准）

在**当前工作区/当前环境**实际执行得到：

| 项 | 结果 | 依据 |
| --- | --- | --- |
| 后端单元/集成测试 | **PASS（208 passed, 0 failed, 23.30s）** | `python -m pytest tests -q`（TRAE 内置 Python 3.10.11） |
| 后端启动 + 冒烟 | **PASS** | `uvicorn ...create_app --factory`：`/health` 200、`/` 200（前端 HTML 117KB）、`/openapi.json` 200（24 路径）、`/documents` 200 |
| 前端静态托管 | **PASS** | 首页由 FastAPI 正确伺服 |
| pytest 之外的功能 | 依赖外部 | 真实 LLM / VLM 问答、真实 Reranker、图片 VLM 链路依赖外部 Key/环境，**本会话未实测** |
| 前端交互 E2E | **NOT RE-RUNNABLE** | 当前工作区不存在 `scripts/e2e.mjs`/Playwright 配置，历史报告的 E2E 无法在本机复现 |
| Docker build/run | **NOT TESTED** | 本机无 Docker（见 §9） |

> 历史文档（`docs/FINAL_RELEASE_TEST_REPORT.md`）记录了更早的完整手工/E2E 验收，但其中引用的
> `scripts/e2e.mjs` 在当前工作区已不存在且部分结论为旧数字（如旧 "82 passed"）；**发布判定应以当前
> `pytest 208 passed` 与上述实测为准**。

---

## 12. Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 分层架构与数据流
- [`docs/ENGINEERING.md`](docs/ENGINEERING.md) — 工程指南（Supported / Available / Not tested）
- [`docs/QUERYNEST_CHANGELOG.md`](docs/QUERYNEST_CHANGELOG.md) — 相对 RAG-Anything 的变更与新增能力
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — 演示脚本
- [`docs/TEST_DATA_REQUIREMENTS.md`](docs/TEST_DATA_REQUIREMENTS.md) — 评测数据要求
- [`docs/FINAL_RELEASE_TEST_REPORT.md`](docs/FINAL_RELEASE_TEST_REPORT.md) — 历史验收报告

---

## 13. License / Attribution

QueryNest 是在 [RAG-Anything](https://github.com/HKUDS/RAG-Anything)（Copyright (c) HKUDS，MIT License）
基础上的二次开发，保留原项目的 `LICENSE` 与署名；本项目同样以 [MIT License](LICENSE) 发布。

- **继承/复用** RAG-Anything 的底层能力：多模态解析、LightRAG 图检索、批处理、缓存、回调/重试等。
- **QueryNest 新增实现**：Query Analyzer / Query Rewrite / Hybrid Retrieval 编排 / BM25 / Reranker 抽象 /
  Citation / Context Builder / Document Management / Evaluation / Model Registry / Provider Adapter /
  Trace / CLI / FastAPI 等。变更明细见 `docs/QUERYNEST_CHANGELOG.md`。

## 14. Limitations（当前未实测 / 依赖外部环境）

- 真实图片 **VLM**（视觉问答）链路：依赖视觉模型 Key，未在当前环境端到端实测。
- 真实 **Reranker** 权重重排：默认 `NoopReranker` 回退；配置真实模型后需自行验证。
- **Docker** 容器运行：本机无 Docker，未实跑（见 §9）。
- Evaluation 的 Faithfulness 当前采用词面启发式占位；无独立判断器时该指标仅作参考。