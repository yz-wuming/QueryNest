"""PHASE 10 hardening 回归测试。

覆盖：
- B1: ``.json`` 支持（config 扩展白名单、批量/压缩包发现、lite 解析、非法 JSON 拒收）
- B2: ``POST /api/evaluation`` 真实执行（真实计算指标，非只读/写死）
- ZIP 安全加固：路径穿越拒收、条目数超限拒收、正常解压可用
"""

import io
import json
import zipfile

import pytest

from querynest.core.config import QueryNestConfig
from querynest.ingestion.lite import LiteTextParser


# ------------------------------------------------------------------ #
# B1 — JSON 支持
# ------------------------------------------------------------------ #
def test_config_supported_extensions_includes_json():
    cfg = QueryNestConfig()
    assert ".json" in cfg.supported_file_extensions


def test_batch_supported_extensions_includes_json():
    from querynest.ingestion.batch_parser import BatchParser

    bp = BatchParser(parser_type="lite", skip_installation_check=True)
    assert ".json" in bp.get_supported_extensions()


def test_lite_parser_parses_json(tmp_path):
    f = tmp_path / "meta.json"
    f.write_text(
        json.dumps({"name": "Golden", "value": 42, "nested": {"k": "v"}}),
        encoding="utf-8",
    )
    blocks = LiteTextParser().parse_document(str(f))
    assert blocks, "JSON 应产出至少一个文本块"
    joined = "\n".join(b["text"] for b in blocks)
    assert "name" in joined and "Golden" in joined and "nested" in joined


def test_lite_parser_rejects_invalid_json(tmp_path):
    from querynest.core.exceptions import DocumentParseError

    f = tmp_path / "bad.json"
    f.write_text("{ this is not : json }", encoding="utf-8")
    with pytest.raises(DocumentParseError):
        LiteTextParser().parse_document(str(f))


# ------------------------------------------------------------------ #
# B2 — Evaluation 真实执行（API 入口）
# ------------------------------------------------------------------ #
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from querynest.api.server import create_app  # noqa: E402


class _StubRetriever:
    async def retrieve_async(self, query, top_k=10):
        return [{
            "document_name": "t.txt",
            "source": "t.txt",
            "score": 0.9,
            "text": "retrieval augmented generation is a method",
            "page": 0,
        }]


class _EvalEngine:
    def __init__(self):
        self._hybrid_retriever = _StubRetriever()

    async def _ensure_initialized(self):
        return None


def test_evaluation_post_runs_real(tmp_path):
    ds = tmp_path / "dataset.json"
    ds.write_text(json.dumps({"examples": [
        {"question": "what is rag", "expected_sources": ["t.txt"]}
    ]}), encoding="utf-8")
    app = create_app(engine=_EvalEngine())
    with TestClient(app) as client:
        r = client.post("/api/evaluation", json={"dataset_path": str(ds), "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body.get("_executed_real") is True
    assert body.get("elapsed_seconds", 0) > 0, "elapsed>0 证明真实执行，非硬编码"
    m = body.get("metrics", {})
    # 命中唯一期望源 => Recall@5 真实计出 1.0
    assert m.get("recall@5") == 1.0


# ------------------------------------------------------------------ #
# ZIP 安全加固
# ------------------------------------------------------------------ #
def _write_zip(entries, name="a.zip"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for arcname, data in entries:
            zf.writestr(arcname, data)
    f = io.BytesIO(buf.getvalue())
    return f


def test_safe_extract_rejects_path_traversal(tmp_path):
    from querynest.api.server import _safe_extract
    from querynest.core.exceptions import QueryNestError

    archive = tmp_path / "evil.zip"
    archive.write_bytes(_write_zip([("../evil.txt", "pwn")]).getvalue())
    out = tmp_path / "out"
    with pytest.raises(QueryNestError):
        _safe_extract(archive, out)
    assert not (tmp_path / "evil.txt").exists()


def test_safe_extract_rejects_many_entries(tmp_path, monkeypatch):
    from querynest.api.server import _ARCHIVE_MAX_ENTRIES, _safe_extract
    from querynest.core.exceptions import QueryNestError

    monkeypatch.setattr("querynest.api.server._ARCHIVE_MAX_ENTRIES", 3)
    archive = tmp_path / "many.zip"
    archive.write_bytes(
        _write_zip([(f"f{i}.txt", "x" * 10) for i in range(5)]).getvalue()
    )
    with pytest.raises(QueryNestError):
        _safe_extract(archive, tmp_path / "out")


def test_safe_extract_valid_zip(tmp_path):
    from querynest.api.server import _safe_extract

    archive = tmp_path / "ok.zip"
    archive.write_bytes(_write_zip([("notes.md", "# hello"), ("data.json", "{}")]).getvalue())
    out = tmp_path / "out"
    _safe_extract(archive, out)
    assert (out / "notes.md").read_text(encoding="utf-8") == "# hello"
    assert (out / "data.json").exists()