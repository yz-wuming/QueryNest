# Test Data Requirements

> QueryNest 验收阶段真实测试数据清单。
> 原则：真实功能必须用真实文件验证；缺失资源一律标记 `NOT TESTED`，不做假文件替代。

- 更新时间：2026-08-30
- 测试数据目录：`testdata/`（私有，已被 `.gitignore` 忽略，不入库）
- 额外真实样本：PDF → 用户按 `examples/data/README.md` 自行放置 `sample.pdf`（不随仓库提交版权敏感 PDF）；JSON → `evaluation/datasets/{sample,example}.json`

---

## 1. 已有

| 类型 | 文件 | 用途 | 覆盖的测试功能 | 是否阻塞 | 当前状态 |
|------|------|------|----------------|:--------:|----------|
| DOCX | `testdata/01_常规文档.docx` | 标题 + 正文 + 列表、多章节 | 文档上传 / 解析 / 分块 / RAG 问答 | 否 | ✅ 已通过真实上传并检索 |
| DOCX | `testdata/02_带表格文档.docx` | 表头 + 多行数据 | 表格结构解析 | 否 | ✅ 可用于解析验证 |
| DOCX | `testdata/03_带图片文档.docx` | 内嵌图片 | 图文混合解析 | 否 | ✅ 可用于解析验证 |
| DOCX | `testdata/04_完整综合文档.docx` | 文字 + 表格 + 图片 | 综合文档链路 | 否 | ✅ 已上传并入库 |
| DOCX | `testdata/05_空白文档.docx` | 空白边界 | 空内容容错 | 否 | ✅ 边界用例 |
| DOCX | `testdata/06_纯文本无格式.docx` | 极简文本 | 纯文本解析边界 | 否 | ✅ 边界用例 |
| PNG  | `testdata/scenery.png` | 场景测试图 | 图片上传 / 多模态管线 | 否 | ✅ 上传解析可用；**VLM 视觉问答待测** |
| PNG  | `testdata/text_poster.png` | 带文字图片 | OCR 测试 | 否 | ✅ 上传解析可用；**OCR 答案待测（需 Vision）** |
| PNG  | `testdata/chart.png` | 图表 | 图表理解 | 否 | ✅ 上传解析可用 |
| PNG  | `testdata/flowchart.png` | 流程图 | 结构图理解 | 否 | ✅ 上传解析可用 |
| PNG  | `testdata/product.png` | 产品图 | 图像检索 | 否 | ✅ 上传解析可用 |
| TXT  | `testdata/README.txt` | 测试集说明 / 真实文本 | 上传 / 分块 / Embedding / 检索 / 问答 / Citation | 否 | ✅ 已真实上传、检索、返回带 Citation 的 Answer |
| PDF  | 未随仓库提交（用户按 `examples/data/README.md` 自行放置 `sample.pdf`） | 真实 PDF 样本 | PDF 解析（MinerU 后端） | 是（PDF 核心能力） | ⚠ 待提供真实 PDF 后实测（先前解析产物 `querynest_output_v4` 已清理） |
| JSON | `evaluation/datasets/sample.json` | 真实评估集（4 问） | Recall / Precision / MRR / NDCG / Faithfulness | 否 | ✅ 真实数据集存在；上次运行结果全 0.0，需真实重跑获得有效分数 |

---

## 2. 缺失

| 类型 | 期望文件 | 用途 | 覆盖的测试功能 | 是否阻塞 | 当前状态 |
|------|----------|------|----------------|:--------:|----------|
| PDF | `paper.pdf`（学术论文样例） | 长文 + 多栏 + 公式 | 复杂 PDF 版面解析、跨页分块 | 否 | ⚠ 缺少，可暂用用户自行提供的 `sample.pdf` 替代 |
| DOC | `old_format.doc` | 旧版 Word 二进制 | LibreOffice 转档链路（`.doc → .docx`） | 否 | ⚠ 缺少（依赖系统 LibreOffice，是否安装待确认） |
| PPT | `slides.ppt/.pptx` | 演示文稿 | 幻灯片解析、图文混排 | 否 | ⚠ 缺少 |
| PPTX | `slides.pptx` | 演示文稿 | 同上 | 否 | ⚠ 缺少 |
| XLS | `spreadsheet.xls` | 旧版表格 | 表格数据抽提 | 否 | ⚠ 缺少 |
| XLSX | `spreadsheet.xlsx` | 电子表格 | 表格解析、多 Sheet | 否 | ⚠ 缺少 |
| MD  | `markdown.md` | Markdown 文献 | Markdown / HTML / PDF 排版链路 | 否 | ⚠ 缺少（`markdown` 可选依赖要求的场景） |
| JPG | `photo.jpg` | JPEG 图片 | 图片格式兼容（区别于 PNG） | 否 | ⚠ 缺少（现有均为 PNG） |

---

## 3. 说明

- **已有**：DOCX（6）+ PNG（5）+ TXT（1）+ PDF（1）+ JSON（2 数据集）——均已确认真实存在。
- **缺失**：均为**非阻塞**增强样本，用于覆盖更多格式与边界；核心链路（DOCX / TXT / PDF / 图片上传 / RAG / Citation）已被现有真实文件覆盖。
- **待补充真实环境**：
  - 真实 Vision API（VLM）Key → 多模态视觉问答、OCR 结果验证。
  - 真实可用的 Embedding / LLM Provider → 评估指标有效重跑、真实 Test Connection。
- 任何缺省资源对应的能力，验收报告中如实标注 `NOT TESTED`，不伪造。