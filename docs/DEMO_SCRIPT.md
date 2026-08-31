# QueryNest — Demo 演示脚本（3–5 分钟）

> 面向“输入 → 检索 → 理解 → 答案 → Citation → Evaluation”的价值演示。
> 演示前需：启动后端（`uvicorn querynest.api.server:app`），并已上传一份含
> **文本 + 表格 + 图表 + 图片**的多模态样例文档（见 §7 准备建议）。

---

## 0:00 · 开场

打开 QueryNest 首页（`http://localhost:9623/`）。

> “QueryNest 是一个面向复杂文档智能分析的多模态 RAG 系统：PDF / 图片 / Word / Excel / PPT
> 进入后 → 多模态解析 → 混合检索（向量 + BM25 + 知识图谱）→ 重排 → 上下文构造 →
> 大模型生成**带源头引用**的答案。全程可插拔、可评测、可解释。”

---

## 0:30 · Documents（多模态入库）

导航左侧 **Documents** 页。

- 点击上传，选择样例文档（**PDF + 含表格 + 图表 + 图片的图文混合文档**）。
- 展示解析结果：`Content types` 区分 **text / table / image / chart**。
- 展示状态链路：`uploaded → parsing → indexed → ready`。
- 提示：“表格、图表、图片不是被简单丢弃，而是被**结构化索引**，检索时能按需召回。”

---

## 1:10 · Chat — 文本问答 + Citation

切到 **Chat**，发送：

> “这份文档的核心结论是什么？”

- 展示 **Answer** + 右下角 **References / Citation**，点击引用可回看支撑原文。
- 强调：“每个答案都附**真实来源**，不编造。”

---

## 1:40 · 表格问答

再问：

> “Q2 哪个地区销量最高？”

- 展示系统正确命中**表格块**并回答（如“上海，2100”）。
- 强调：“传统文本 RAG 到这里往往就丢了，而 QueryNest 把表格结构化提取出来进行检索。”

---

## 2:00 · 图片 / 图表问答

问（若样例含图表）：

> “图中三个产品的性能分别是多少？哪个最高？”

- 展示多模态理解（图表召回 → 92 / 85 / 78 → Alpha X1 最高）。
- 强调图片/图表信息参与检索与生成。

---

## 2:30 · 跨模态问答

问：

> “结合文字里的参数和图表里的性能，Alpha X1 是不是性能最高的产品？”

- 展示 **Cross-modal RAG**：文本参数 + 表格销量 + 图表性能共同进入上下文生成回答。

---

## 3:00 · Evaluation（评测）

进入 **Evaluation** 页，点击运行/刷新。

- 展示真实指标：**Recall / MRR / NDCG / Precision / Faithfulness**。
- 强调：“这些指标来自**真实检索驱动**的评测集，不是写死的静态数字。”

---

## 3:30 · Models / API

- **Models**：展示模型注册表、Provider 适配（OpenAI 兼容 / Ollama 等）、聊天模型与多模态模型。
- **API**：展示 `POST /query`、`POST /documents`、`GET /health` 等端点与请求示例。

---

## 4:00 · 收尾：架构 / 项目 / GitHub

- 一句话总结技术栈：FastAPI · LightRAG(Graph) · 混合检索 · 可插拔 Reranker · 前端单页。
- 展示 **README** 的架构图与 **RAG Pipeline**。
- 若发布完成，指向 **GitHub Repository** 与 License。

> 收尾话术：“Document 进 → Hybrid Retrieval → LLM/VLM 生成 → **可引用答案 + 可评测指标**。
> 这就是 QueryNest——不是黑盒 RAG，而是每一层都可读、可测、可解释的工程实现。”

---

## 全程要点

- **不展示大量代码**；聚焦产品价值：入库 → 检索 → 理解 → 带引用答案 → 评测。
- 每个页面停留 ≤15s，节奏轻快。
- 多模态 / 跨模态 / Citation / Evaluation 是最有区分度的四段，务必讲清“系统做了什么”。

---

## 演示环境准备建议

1. 正式演示使用带真实图片/图表的样例（不依赖本文档单片测试数据）。
2. 提前完成一次入库（首次解析含 LLM 抽取，可能 2–3 分钟），演示时直接问答。
3. 准备备用：若在线模型慢，预留浅问答；进入 Demo 前先各触发一次热缓存。
4. 给模型配置 rerank（可选），在 Evaluation 页展示重排后更佳结果。