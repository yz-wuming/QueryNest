"""Document parsing / registry / metadata association tests.

Engine-dependent (MinerU/LightRAG) end-to-end paths are guarded so the default
suite stays lightweight and dependency-free.
"""

import pytest

from querynest.core.models import ContentType, DocumentMetadata


def test_document_metadata_dict_roundtrip():
    meta = DocumentMetadata(
        document_id="d1", filename="paper.pdf", file_type="pdf", page_count=12,
        parser="mineru", parse_method="auto",
        content_types=["text", "image", "table"], source_path="/tmp/paper.pdf",
    )
    d = meta.to_dict()
    assert d["document_id"] == "d1"
    assert d["content_types"] == ["text", "image", "table"]
    # 还原
    restored = DocumentMetadata(**d)
    assert restored.filename == "paper.pdf"
    assert restored.page_count == 12


def test_document_metadata_defaults():
    meta = DocumentMetadata(document_id="x", filename="a.txt")
    assert meta.file_type == ""
    assert meta.page_count == 0
    assert meta.content_types == []


def test_parser_registry():
    """解析器注册表（需在无 mineru 环境下也可安全调用）。"""
    from querynest.ingestion.parser import (
        SUPPORTED_PARSERS,
        get_supported_parsers,
        list_parsers,
    )

    parsers = list_parsers()
    assert isinstance(parsers, dict)
    assert set(parsers).issuperset(set(get_supported_parsers()))
    # 注册表至少包含框架声明支持的解析器
    assert "mineru" in SUPPORTED_PARSERS


def test_content_type_mapping():
    assert ContentType.IMAGE.value == "image"
    assert ContentType.TABLE.value == "table"
    assert ContentType.EQUATION.value == "equation"
    assert ContentType.has_value("table")
    assert not ContentType.has_value("nonsense")


def test_get_parser_unknown_raises():
    """未知解析器抛 ValueError。"""
    from querynest.ingestion.parser import get_parser

    with pytest.raises(ValueError):
        get_parser("definitely_not_a_parser")


def test_lite_parser_is_registered():
    """QueryNest 轻量文本解析器 'lite' 已注册为内置。"""
    from querynest.ingestion.parser import SUPPORTED_PARSERS, get_parser

    assert "lite" in SUPPORTED_PARSERS
    parser = get_parser("lite")
    assert parser.check_installation() is True


def test_lite_parser_parses_text(tmp_path):
    """lite 解析器直接从 .txt 生成文本块 content_list。"""
    f = tmp_path / "notes.txt"
    f.write_text("上一段说明。\n\n这是第二段关于混合检索的内容。\n\n第三段。", encoding="utf-8")
    from querynest.ingestion.parser import get_parser

    parser = get_parser("lite")
    content_list = parser.parse_document(f)
    assert content_list
    assert all(item.get("type") == "text" for item in content_list)
    texts = " ".join(i.get("text", "") for i in content_list)
    assert "混合检索" in texts


def test_lite_parser_rejects_pdf(tmp_path):
    from querynest.core.exceptions import DocumentParseError
    from querynest.ingestion.parser import get_parser

    f = tmp_path / "not_txt.pdf"
    f.write_bytes(b"%PDF-1.4\n")
    parser = get_parser("lite")
    with pytest.raises(DocumentParseError):
        parser.parse_document(f)