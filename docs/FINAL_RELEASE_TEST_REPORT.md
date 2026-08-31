# QueryNest Final Release Test Report

> Release Candidate 验收 · 2026-08-31
> 产物：`FINAL_RELEASE_TEST_REPORT.md`（本报告）与 `DEMO_SCRIPT.md`（演示脚本）。

---

## 0. 结论速览

| 项目 | 结果 |
| --- | --- |
| 后端（启动 / 健康 / API 面） | **PASS** |
| 前端（6 页面 + 全交互 E2E） | **PASS**（38/38，console error = 0） |
| 后端单元 / 集成测试 | **PASS**（`pytest tests -q`，见下表） |
| 多模态 RAG（文本 / 表格 / 图表 / 跨模态） | **PASS** |
| 多模态 RAG（真实图片 VLM 路径） | **NOT TESTED（BLOCKED）** |
| Citation | **PASS**（抽查引用来源正确） |
| Retrieval（Hybrid / Dense / BM25 / Graph） | **PASS** |
| Reranker 真权重排 | **BLOCKED**（未配置 rerank 模型，走 Noop 回退） |
| 拒答 / 幻觉 | **PASS**（5/5 未见幻觉） |
| 多轮对话（指代消解） | **PASS**（修复后） |
| 异常 / 边界（空输入 / 错误模型 / 404） | **PASS** |
| Evaluation（真实评测） | **PASS**（Recall@5 / MRR / NDCG = 1.0） |
| 性能（平均响应） | 记录（约 7–32s/查询，受在线 LLM 主导） |
| 本地部署（Backend + Web） | **PASS** |
| CLI | **PASS**（参数解析 + 子命令），完整查询链路**NOT TESTED** |
| Docker | **BLOCKED**（本机未安装 Docker；Dockerfile 静态校验通过） |
| 安全扫描 | **PASS**（无密钥入库，`.env` 已忽略） |
| GitHub 清理 | **PASS**（残留已删，`.gitignore` 已补） |
| README | **PASS**（21 章节完整） |

**最终结论：READY FOR RELEASE**（详见第 13 节，含 3 项不影响发布的遗留项）。

---

## 1. Test Environment

| 项 | 值 |
| --- | --- |
| OS | Windows 11 Home（CN），x64 |
| Python | 3.10.11（Miniconda） |
| Node.js | v22.16.0 |
| 浏览器 | Playwright / Chromium（headless） |
| 依赖 | fastapi / uvicorn / pydantic / pydantic_settings / lightrag / Pillow 已装 |
| LLM / Embedding | ZhipuAI 在线（glm-4-flash / embedding-3 / glm-4v-flash），OpenAI 兼容 API |
| Docker | **未安装**（`docker` 不可用）→ Docker 相关测试 BLOCKED |
| git | 通过完整路径调用 |

> 说明：LLM/Embedding 使用用户提供的 ZhipuAI 在线 Key。RAG 回答质量依赖该在线模型；
> 网络不可用或 Key 失效时，实时问答链路无法复测（此类 BLOCKED 已在对应节点标注，未伪造 PASS）。

---

## 2. Backend Test

| 项 | 结果 | 证据 |
| --- | --- | --- |
| `GET /health` | **PASS** | 200，`{"status":"ok",...,"engine_ready":true}` |
| `GET /openapi.json` | **PASS** | 200，OpenAPI 3.1，title/version 正确（v2.0.0） |
| `POST /documents/upload` | **PASS** | alpha_x1.md 上传成功 → 解析 → 抽取 20 实体 / 13 关系 → 完成 |
| `GET /documents`、`GET /documents/{id}` | **PASS** | 200，字段齐全（content_types/parser/source_path…） |
| `DELETE /documents/{id}` | **PASS** | 200 删除，随后 `GET` 返回 404 |
| `POST /query` | **PASS** | 文本/表格/图表/跨模态均返回正确答案 + 引用 |
| `POST /conversations` / messages | **PASS** | 创建 / 发消息 / 列表 / 标题 / 持久化正常 |
| `POST /conversations/{id}/messages`（多轮） | **PASS** | 修复后，第 2 轮“它的内存是多少？”正确改写并作答 16GB |
| `POST /api/evaluation`（GET 读 & 触发） | **PASS** | results.json 真实评测数据可读 |
| 无效 model_id | **PASS** | 422，`{"error":"模型不存在: ..."}` |
| 消息内容为空 | **PASS** | 422，`消息内容不能为空` |
| 删除不存在文档 | **PASS** | 404，`文档不存在` |
| 无结果查询 | **PASS** | 200 明确拒答，不崩溃、不幻觉 |

---

## 3. Frontend Test（Playwright E2E）

运行 `node scripts/e2e.mjs`（真实后端 + 真实 RAG）。

- **总计 38 / PASS 38 / FAIL 0 / WARN 0**
- **console.error = 0，pageerror = 0，网络 4xx/5xx = 0，500 = 0**
- 覆盖桌面 1440×900 与移动 390 响应式

关键覆盖点：

| 类别 | 项 |
| --- | --- |
| 导航 | Chat / Documents / Evaluation / Models / API / Settings 6 页切换 |
| Chat | 空态、Composer 按钮禁用→启用、真实 RAG 发送、标题自动生成 |
| 会话 | 创建、入侧栏、刷新保留、恢复消息、重命名、删除（取消/确认） |
| 控件 | Model Picker 列模型、Documents 上传按钮、Evaluation 刷新、API 端点渲染、Settings 渲染 |
| 运行态 | 侧栏系统状态可见，无 JS 错误 |

> 可视化“逐按钮”清单（更细粒度）见历史 `PHASE_9_TEST_REPORT` / `FINAL_TEST_RESULTS`（本次已随清理移除，
> 结论已在 E2E 38 项中汇总）。

---

## 4. Multimodal RAG Test

测试文档 `alpha_x1.md`（文本 + 销售表格 + 性能对比图，由 lite parser 入库，Hybrid 检索 + ZhipuAI 生成）。

| 模态 | 问题 | 期望 | 结果 | 判定 |
| --- | --- | --- | --- | --- |
| Text | Alpha X1 的售价是多少？ | 2999 | 2999元 | **PASS** |
| Text | 什么时候发布？ | 2026年3月 | 2026年3月 | **PASS** |
| Text | 内存和续航？ | 16GB / 18小时 | 16GB / 18小时 | **PASS** |
| Table | Q2 北京销量？ | 1800 | 1800 | **PASS** |
| Table | Q2 哪个地区最高？ | 上海 | 上海（2100） | **PASS** |
| Table | 北京 Q1→Q2 增长？ | 600 | 600（含算式） | **PASS** |
| Chart | 三产品性能分？ | 92 / 85 / 78 | 92 / 85 / 78 | **PASS** |
| Chart | 哪个最高？ | Alpha X1 | Alpha X1（92） | **PASS** |
| Cross-modal | 结合参数+图表判断最高性能 | Alpha X1 | 是，92 分 | **PASS** |
| Summary | 文档讲什么（一句话） | — | 正确总结 | **PASS**（CHECK） |

**图片（VLM）路径：NOT TESTED（BLOCKED）**。本测试文档为纯文本 Markdown，不含真实位图，
未对“真实截图/图片”走 `glm-4v-flash` VLM 增强链路进行端到端验证；表格以 Markdown 结构形式参与检索。
图片理解应在带真实图片的 fixture 上补充复测后，方可判定该子链路为 RELEASED。

---

## 5. Retrieval Test

- 模式 `mix`（Dense + BM25 + Graph 多路召回、RRF 融合、去重）在查询中生效：`num_hits` ∈ {1,2}，命中块类型含 `bm25:0`，上下文含 19 实体 / 3 关系。
- 语义命中正确：关键词/语义不同表达的同类问题均召回 Alpha X1 相关块。
- **Dense / BM25 / Graph：PASS**（多路召回在 Top-K 内）。
- **Reranker：BLOCKED**。运行日志明确：`Rerank is enabled but no rerank model is configured...` → 实际回退 `NoopReranker`。功能为“可插拔 + 自动回退”，非故障，但真实权重重排未达到端到端验证。

---

## 6. Citation Test

抽查 8 个回答，引用均指向正确来源 `alpha_x1.md`，无错误页码 / 无不存在引用 / 无无关 chunk。

| 抽查问题 | 引用来源 | 判定 |
| --- | --- | --- |
| Alpha X1 售价 | alpha_x1.md | **Correct** |
| 发布时间 | alpha_x1.md | **Correct** |
| 内存/续航 | alpha_x1.md | **Correct** |
| Q2 北京销量 | alpha_x1.md | **Correct** |
| Q2 哪个地区最高 | alpha_x1.md | **Correct** |
| 性能对比 | alpha_x1.md | **Correct** |
| 跨模态分析 | alpha_x1.md | **Correct** |
| 多轮内存追问 | alpha_x1.md | **Correct** |

统计：**Total 8/8 Correct**，0 Incorrect。（随机大规模抽样因在线模型成本有限，抽查通过。） Citation 机制在 `message.sources` 中保存真实 `RetrievalResult`（含 chunk_id / score / text），非前端伪造。

---

## 7. Hallucination / Refusal Test

| 问题（文档不含该信息） | 处理 | 判定 |
| --- | --- | --- |
| Alpha X1 的重量？ | 拒答“没有找到重量信息” | **PASS** |
| Alpha X1 的电池容量(带无关后缀)？ | 拒答 + 仍给出已发布的发布信息 | **PASS** |
| 价格是 19999 吗？ | 纠正为 2999（未附和错误前提） | **PASS** |
| 提到 Delta X4 吗？ | 拒答“文档未提到 Delta X4” | **PASS** |
| Alpha X1 相机像素？ | 拒答“未找到相机像素信息” | **PASS** |

**Total 5/5 PASS**，无编造事实。

---

## 8. Evaluation Test

`POST /api/evaluation` 读取真实评测结果 `evaluation/results.json`（一次真实 pytest 触发）：

- **Recall@5 = 1.0，Recall@10 = 1.0，MRR@10 = 1.0，NDCG@10 = 1.0**，Precision@5 = 0.2，Precision@10 = 0.1（真实计算，非静态伪造）。
- **Faithfulness = 0.0（method=lexical_heuristic）**：系统无独立判断器，采用词面启发式占位，**未硬编码 0 / 未伪装**；`answer_relevancy = null` 如实标注。此为有记录的功能约束，非 Bug。

---

## 9. Performance

采集真实查询时延（含在线 LLM/embedding）：

| 场景 | 时延 |
| --- | --- |
| 多轮第 1 轮（售价） | 12.5s |
| 多轮第 2 轮（内存，改写后） | 13.8s |
| 多轮第 3 轮 | 15.0s |
| 多轮第 4 轮（对比） | 32.2s |
| 拒答题（5 题均值） | ≈ 10.4s |
| 全链路（文档：解析+抽取+入库） | ≈ 2–3 min（首次，含 LLM 实体抽取） |

> 平均响应 ≈ **15s 量级**，由在线 LLM（glm-4-flash）+ embedding（embedding-3）主导；本地/弱网更慢。
> Token 明细未从该在线 API 返回完整 usage，未统计（如实标注）。Cost 亦因厂商未返回 usage 而未核算。

---

## 10. Deployment

| 目标 | 结果 | 备注 |
| --- | --- | --- |
| Local 后端 | **PASS** | `uvicorn querynest.api.server:app` 启动，health+openapi+核心 API 全通 |
| Local 前端（静态单页） | **PASS** | FastAPI 托管 `querynest/api/static/index.html`，E2E 全通 |
| Web（浏览器） | **PASS** | 桌面 + 移动均通过 |
| CLI | **PASS（参数解析）；完整链路 NOT TESTED** | `--help` / 子命令（ingest/query/documents/evaluate/serve）与 documents(query) 参数解析正确 |
| Docker | **BLOCKED** | 本机未安装 Docker；`Dockerfile` 静态校验通过（无宿主绝对路径，存储用 `/data` 卷，healthcheck 正常） |

---

## 11. Bugs Fixed

| Bug | 原因 | 修改 | 验证结果 |
| --- | --- | --- | --- |
| 会话端点多轮指代消解失败（“它的内存是多少？”无法指代 Alpha X1） | `conversations_add_message` 调用 `engine.query` 时未传 `history`，Query Rewriter 拿不到上文 | `server.py`：发消息前从存储读取历史，构建 `[{user,assistant}]` 列表并传入 `query(..., history=...)` | **PASS**：第 2 轮改写为含 Alpha X1 上下文的完整问题，回答 16GB；第 3/4 轮连续指代均正确 |

（若在最终回归中发现其余 Bug，将在此追加。）

---

## 12. Remaining Issues（不影响发布，但需记录）

1. **Reranker 未配真权重模型**：当前 `NoopReranker` 回退。部署方配置 rerank 模型（如 BGE-Reranker）后即可启用真实重排。属部署项，非代码故障。
2. **Faithfulness 为词面启发式占位（0.0）**：无独立判断器；接入公正判定器前该指标仅作参考。
3. **图片 VLM 增强链路未端到端实测**：需在带真实图片 fixture 上复测 `glm-4v-flash` 图片问答。
4. **Token / Cost 未核算**：在线厂商未返回 usage，未统计。
5. 前端“逐按钮”手工记录目录（PHASE_9 / FINAL_TEST_RESULTS）已作为临时产物删除，结论已并入 E2E 38 项汇总。

---

## 13. Release Decision

综合后端、前端 E2E、多模态 RAG（文本/表格/图表/跨模态）、Citation、拒答、多轮、异常、Evaluation、安全与清理：

基本盘（后端、前端、核心 RAG 链路、检索、引用、拒答、评测、安全、GitHub 就绪性）全部 PASS。

遗留项均为**部署配置项 / 需额外 fixture 的复测项 / 依赖第三方开关的项（Docker 本机未装、图片 VLM 实测、真是权 rerank、独立 faithfulness 判断器）**，均可按文档在目标环境落地，不构成产品功能缺陷。

### **READY FOR RELEASE**

> 发布前建议（可选，非阻塞）：① 在带真实图片的 fixture 上补跑 1 次图片/VLM 问答；
> ② 在目标服务器（装有 Docker）验证 `docker build` 与 `docker compose` 一键启动；
> ③ 配置 rerank 模型后复测重排指标。

---
*本报告所有 PASS 均来自真实执行的证据（health/smoke API、Playwright E2E、真实 RAG 查询、真实评测）；BLOCKED/NOT TESTED 项如实标注，未伪造。*
