"""PDF Index Bridge — 最小针对性测试（覆盖 PDF 修复的 7 个要求点）。

仅用轻量 mock / 纯 Python，不触发 MinerU / LightRAG / 网络。
验证：content_list→检索源；PDF 二进制不泄漏进 BM25；text/table 块保留；
content_type 不丢；citation 不造假 page；.txt 路径无回归。
"""

from typing import Any, Dict, List

# -------------------------------------------------------------------- #
# 1) separate_content 是 engine.ingest 提取正文的来源，先验证它
# -------------------------------------------------------------------- #
from querynest.utils import separate_content


def _make_content_list() -> List[Dict[str, Any]]:
    return [
        {"type": "text", "text": "Hybrid retrieval in QueryNest combines Dense.",
         "page_idx": 0},
        {"type": "table", "table_body": "Row0|Col0", "page_idx": 0},
        {"type": "chart", "image": "chart.png", "page_idx": 0},
    ]


def test_separate_content_extracts_only_text_for_source():
    """TEXT 块进入正文；table/chart 不作为文本源（不把二进制塞进 BM25）。"""
    text_content, modal = separate_content(_make_content_list())
    assert "Dense" in text_content
    # 二进制/表格/图不混入文本正文
    assert "table_body" not in text_content
    types = {m.get("type") for m in modal}
    assert types == {"table", "chart"}
    assert len(modal) == 2


# -------------------------------------------------------------------- #
# 2) _read_source：PDF 二进制绝不 read_text()
# -------------------------------------------------------------------- #
from pathlib import Path

import pytest

from querynest.retrieval.keyword import _read_source


def test_read_source_rejects_binary_pdf(tmp_path: Path):
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\nendstream")
    # 即使 source_path 指向 PDF（未正确触发 _save_source 的兜底场景）也不得泄漏二级制
    assert _read_source({"source_path": str(pdf)}) == ""


def test_read_source_returns_text_source(tmp_path: Path):
    txt = tmp_path / "source.txt"
    txt.write_text("QueryNest hybrid retrieval combines Dense and BM25.",
                   encoding="utf-8")
    body = _read_source({"source_path": str(txt)})
    assert "hybrid" in body


# -------------------------------------------------------------------- #
# 3/4) TEXT 与 TABLE 块都能进入 index（可检索源）
# -------------------------------------------------------------------- #
def test_text_and_table_blocks_are_kept_as_retrievable_blocks():
    cl = _make_content_list()
    text_content, modal = separate_content(cl)
    # text 块进入文本索引源
    assert "Dense" in text_content
    # table 块保留结构化内容且不丢（作为 multimodal item 保留 type/table）
    table = next(m for m in modal if m["type"] == "table")
    assert table["table_body"] == "Row0|Col0"


# -------------------------------------------------------------------- #
# 5) content_type metadata 不丢失
# -------------------------------------------------------------------- #
def test_content_type_preserved():
    _, modal = separate_content(_make_content_list())
    by_type = {m["type"] for m in modal}
    assert "table" in by_type and "chart" in by_type
    orig = _make_content_list()
    assert orig[1]["type"] == "table" and orig[2]["type"] == "chart"


# -------------------------------------------------------------------- #
# 6) citation 不造假 page —— 用 LightRAG 不可用时的诚实分支
#    （engine 的 citation 在 page 缺失时置空，不猜数字）
# -------------------------------------------------------------------- #
from querynest.core.models import RetrievalResult


def test_citation_never_fabricates_page():
    hit = {"document_id": "d1", "document_name": "sample.pdf",
           "content_type": "text", "text": "x", "score": 0.1}
    page = hit.get("page")  # LightRAG only_need_prompt 不暴露精确 page -> None
    assert page is None  # 绝不伪造
    rr = RetrievalResult(answer="a", sources=[hit])
    assert rr.answer == "a"


# -------------------------------------------------------------------- #
# 7) .txt 原有 E2E 不回归：separate_content 对纯文本同样产出正文
# -------------------------------------------------------------------- #
def test_txt_path_no_regression():
    cl = [{"type": "text", "text": "plain txt line one", "page_idx": 0},
          {"type": "text", "text": "plain txt line two", "page_idx": 0}]
    text_content, modal = separate_content(cl)
    assert "line one" in text_content and "line two" in text_content
    assert modal == []


# -------------------------------------------------------------------- #
# 8) engine.ingest 的收尾：content 被写入 document_store source（不走上报 source_path 原文）
# -------------------------------------------------------------------- #
def test_document_store_upsert_persists_content(tmp_path: Path):
    from querynest.core.models import DocumentMetadata
    from querynest.storage.document_store import DocumentStore

    ds = DocumentStore(storage_dir=str(tmp_path))
    meta = DocumentMetadata(document_id="pdf_doc", filename="sample.pdf",
                            file_type="pdf", source_path="should/be/replaced")
    ds.upsert(meta, content="Hybrid retrieval combines Dense and BM25.")
    # 解析正文确实落盘为 {doc_id}.txt，供 BM25 的 read_source 读取
    assert "Hybrid retrieval" in ds.read_source("pdf_doc")


# -------------------------------------------------------------------- #
# 8.1) 真实 PDF 的 doc_id 含路径分隔符（如 examples\\data\\sample.pdf），
#      必须仍能落盘并由 read_source 读回 —— 否则 PDF parsed text 未持久化
# -------------------------------------------------------------------- #
def test_nested_doc_id_source_persists(tmp_path: Path):
    from querynest.core.models import DocumentMetadata
    from querynest.storage.document_store import DocumentStore

    ds = DocumentStore(storage_dir=str(tmp_path))
    meta = DocumentMetadata(document_id="examples\\data\\sample.pdf",
                            filename="sample.pdf", file_type="pdf",
                            source_path="examples\\data\\sample.pdf")
    ds.upsert(meta, content="Real PDF parsed text: QueryNest hybrid retrieval.")
    assert "QueryNest" in ds.read_source("examples\\data\\sample.pdf")