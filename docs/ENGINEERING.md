# QueryNest — Engineering Guide

> 面向工程师的架构与能力说明。本文档说明 QueryNest 如何做到 **可解释、可测试、可评估、可观测**，
> 并从工程角度区分 **Supported / Available / Not tested**，避免夸大（不宣称 state-of-the-art）。

- 前端：单文件 `querynest/api/static/index.html`（原生 HTML/CSS/JS，无框架）
- 后端：FastAPI + LightRAG，接口见 `querynest/api/server.py`

---

## 1. 架构总览

```
        Frontend (single-file index.html)
            │  HTTP/JSON
            ▼
        API (FastAPI: /query, /documents, /models, /api/evaluation, /api/traces)
            │
            ▼
        QueryService (QueryNest core engine)
            │
   ┌────────┴──────────────┐
   ▼                       ▼
Query Analysis         Document Pipeline
Query Rewrite          Parser → Chunker → Embedding → Index
Hybrid Retrieval
Vector(Dense) ─ BM25(Keyword) ─ Graph
      └───── RRF Fusion ─────┘
              │
          Reranker (optional)
              │
         Context Builder
              │
          LLM / VLM
              │
          Citation
              │
         Trace / Evaluation
```

**文档链路**：`Document → Parser → Chunker → Embedding → Index`

**查询链路**：`Query → Analysis → Rewrite → Vector/Keyword/Graph → Fusion(RRF) → Rerank → Context → LLM/VLM → Citation → Trace`

---

## 2. Multi-Provider Model Gateway

- `querynest/core/model_registry.py`：模型 CRUD / 启停 / 默认 / 于请求作用域路由。
- `querynest/core/providers.py` + `querynest/core/clients.py`：Provider Adapter（OpenAI / DeepSeek / Qwen / 智谱 GLM / DashScope / OpenAI-Compatible / Ollama …，Anthropic / Gemini 预留）。

可用性分级（README 也遵循）：
- **Supported**：已被 Adapter 覆盖。
- **Available**：当前环境有凭据可实测。
- **Not tested**：仅架构预留、未用真实 Key 验证（例如 Anthropic / Gemini）。

---

## 3. Hybrid Retrieval

`querynest/retrieval/hybrid.py` 提供 Dense(向量) + Keyword(BM25) + Graph 三路召回，经 RRF 融合去重，
可选重排。检索器即回调（`RetrieverLike`），不重复实现底层向量库。

**检索模式（`mode`）**：
- `local`：Dense（向量）局部检索
- `global`：图检索
- `mix`：混合（默认，多路融合）

---

## 4. Retrieval Ablation（检索消融）

`querynest/evaluation/ablation.py` 提供 `run_ablation`：在**同一数据集 / 同一条查询 / 同一 ground truth / 同一 K /
同一评估函数**下，真实执行多个检索策略（vector / keyword / hybrid / hybrid_rerank），计算
Recall@K / Precision@K / MRR / NDCG，并记录**真实** latency（在调用处用 `time.perf_counter` 计时，
绝不伪造 timing；未提供策略标记 `not_available`）。

引擎把真实检索组件暴露为策略回调：`engine.retrieval_strategies()`（需先初始化检索器）。

可复现入口：

```bash
python scripts/run_evaluation.py --dataset evaluation/datasets/sample.json --top-k 5
python scripts/run_evaluation.py --list-datasets
```

> 诚实说明：`hybrid_rerank` 只有在引擎激活了 Reranker 时才可供执行；否则如实标 `not_available`，
> 不会用占位值冒充。底层引擎需先完成文档索引并有可用 Embedding，否则脚本如实报 `BLOCKED`。

---

## 5. Evaluation Benchmark

`querynest/evaluation/`：
- `dataset.py`：加载 JSON/JSONL 数据集（`question` / `expected_answer` / `expected_sources` / `metadata`）。
- `metrics.py`：`recall_at_k` / `precision_at_k` / `mrr` / `ndcg_at_k` / 词语 Faithfulness（可注入 judge/embedding）。
- `runner.py`：`EvalRunner` 对每条样例调用检索回调，聚合指标写入 `evaluation/results.json`；faithfulness /
  answer relevancy 在缺少判定器时标 `skipped`，不伪造。

评测集已含：`evaluation/datasets/{example,sample}.json`。

---

## 6. Query Trace（可观测性）

`querynest/core/trace.py`：每步真实计时（`time.perf_counter` start→end），进程内 LRU `trace_store`。

一次真实查询会产生：`query_analysis → query_rewrite → (可选 vlm_enhanced) → retrieval → rerank → context_builder → generation → citation`，每步含 `name/status/latency_ms/metadata`，失败记录 `step + error`，
可用 Trace 回答“这次查询为什么失败”。

**绝不记录** API Key / Secret / Authorization / 密码 / token / Cookie —— `test_trace_contains_no_secrets`
守卫该约束。

Trace 已注入真实查询：`/query` 响应 `metadata.trace_id` 指向轨迹，可经 API 独立获取：

```bash
GET /api/traces            # 最近轨迹
GET /api/traces/{trace_id} # 单次完整轨迹
```

---

## 7. Citation

`querynest/query/citation.py`：把命中引用转换为规整 `Citation(document, page, type, score, chunk)`。
若检索未拿到上下文，宁可**不产生引用**，也不会生成“假来源”；`engine.py` 对 `no-context` 命中做守卫。

---

## 8. Reranker（支持但默认不激活）

- `querynest/retrieval/reranker.py`：`BaseReranker` / `NoopReranker` 抽象；`engine.enable_rerank` 控制是否接入。
- 接入后作最后一层重排，可提升上下文质量；但**默认关闭**以降低外部依赖与本机负载。
- 配置：在 `Settings` / 配置中提供 `reranker_model` 与 `enable_rerank`。
- 状态：**Supported**（architecture-adapter 提供），当前环境未使用真实 Reranker Key 实测 → **Not tested**，
  因此不声称“配置后一定变好”，需用户在配置真实 reranker 后自行跑 §4 的 ablation 验证。

---

## 9. 测试与观测保障

| 项 | 说明 |
|----|------|
| 后端单测 | `python -m pytest tests -q`（含 evaluation / ablation / trace / citation / metrics） |
| 编译 | `python -m compileall querynest` |
| 前端 E2E | `node scripts/e2e.mjs`（需安装 Playwright；当前环境模块解析受限 → BLOCKED，诚实标注） |
| 隐藏物 | `git diff --check`；`scripts/secret_scan.py`（secret 仅存在本地 `.env`，由 `.gitignore` 排除） |

> 工程价值主张（如实表述）：**engineering-focused、可复现 benchmark、已评估的 pipeline**，而非任何
> 无依据的 superlative。

---

## 10. 限制（Limitations）

- 真实 VLM（视觉问答）、真实 Provider Test Connection、真实 Reranker 需外部 Key / 环境，当前标 **NOT TESTED**。
- Retrieval Ablation 的数值只在“已索引真实文档 + 可用 Embedding/LLM”的环境中才产生，环境缺失时脚本如实 `BLOCKED`。
- Playwright E2E 依赖 Node 模块 `playwright`，未安装时无法运行（见上），不视为产品缺陷。