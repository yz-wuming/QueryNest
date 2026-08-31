"""FastAPI service tests (skipped if fastapi/httpx not installed)."""

import pytest

from querynest.core.models import Citation, RetrievalResult

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

try:  # TestClient 需要 httpx
    TestClient  # noqa: B018
    _HAS = True
except Exception:  # pragma: no cover
    _HAS = False

from querynest.api.server import create_app  # noqa: E402


class _FakeEngine:
    """轻量假引擎：避免在测试中引入 lightrag。"""

    def __init__(self):
        self.store = {}

    def list_documents(self):
        return list(self.store.values())

    def get_document(self, document_id):
        row = self.store.get(document_id)
        if not row:
            raise KeyError(document_id)
        return row

    def delete_document(self, document_id):
        if document_id not in self.store:
            return False
        del self.store[document_id]
        return True

    async def ingest(self, path, parse_method=None, doc_id=None, **kw):
        did = doc_id or "auto-doc"
        self.store[did] = {"document_id": did, "filename": "paper.pdf", "status": "ready"}
        return _Meta(did, "paper.pdf")

    async def query(self, query, **kw):
        return RetrievalResult(
            answer=f"answer for {query}",
            sources=[Citation(document_name="paper.pdf", page=2, content_type="table", score=0.91)],
            retrieval={"num_hits": 1, "intent": "table"},
        )

    async def query_multimodal(self, query, multimodal_content=None, **kw):
        return RetrievalResult(answer="multimodal answer", retrieval={"multimodal": True})


from querynest.core.models import DocumentMetadata as _Meta  # noqa: E402


@pytest.fixture
def client():
    app = create_app(engine=_FakeEngine())
    return TestClient(app)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "querynest"


def test_documents_list(client):
    r = client.get("/documents")
    assert r.status_code == 200
    assert "documents" in r.json()


def test_documents_upload_and_get(client):
    r = client.post("/documents", json={"path": "paper.pdf", "document_id": "docx"})
    assert r.status_code == 200
    assert r.json()["document_id"] == "docx"
    g = client.get("/documents/docx")
    assert g.status_code == 200
    assert g.json()["filename"] == "paper.pdf"


def test_documents_delete(client):
    client.post("/documents", json={"path": "paper.pdf", "document_id": "d1"})
    r = client.delete("/documents/d1")
    assert r.status_code == 200
    assert client.delete("/documents/d1").status_code == 404


def test_query_endpoint(client):
    r = client.post("/query", json={"query": "哪个模型效果最好？"})
    assert r.status_code == 200
    body = r.json()
    assert "answer for" in body["answer"]
    assert body["sources"][0]["type"] == "table"
    assert body["sources"][0]["page"] == 2


def test_query_multimodal_endpoint(client):
    r = client.post(
        "/query/multimodal",
        json={"query": "这是什么？", "content": [{"type": "image", "content": "/x.png"}]},
    )
    assert r.status_code == 200
    assert r.json()["answer"] == "multimodal answer"


def test_query_missing_path_rejected(client):
    r = client.post("/documents", json={})
    assert r.status_code == 400


# ---------------- Query Trace (observability) ----------------
def test_trace_detail_endpoint(client):
    from querynest.core.trace import QueryTrace, trace_store

    tr = QueryTrace(query="什么是 Hybrid Retrieval？", mode="mix")
    tr.mark("query_analysis", metadata={"intent": "text"})
    tr.mark("vector_retrieval", metadata={"count": 3})
    tr.finalize(citations=["paper.pdf"])
    trace_store.put(tr)

    r = client.get(f"/api/traces/{tr.trace_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["trace_id"] == tr.trace_id
    assert body["query"] == "什么是 Hybrid Retrieval？"
    assert body["status"] == "completed"
    names = [s["name"] for s in body["steps"]]
    assert names == ["query_analysis", "vector_retrieval"]
    assert body["num_citations"] == 1


def test_trace_detail_404(client):
    r = client.get("/api/traces/does-not-exist-000")
    assert r.status_code == 404


def test_trace_contains_no_secrets():
    from querynest.core.trace import QueryTrace, trace_store

    tr = QueryTrace(query="q", model_id="m")
    tr.mark("generation", metadata={"model": "deepseek", "provider": "deepseek"})
    tr.finalize(citations=["paper.pdf"])
    trace_store.put(tr)
    client = TestClient(create_app(engine=_FakeEngine()))
    r = client.get(f"/api/traces/{tr.trace_id}")
    raw = r.text.lower()
    for secret_word in ("api_key", "authorization", "bearer", "sk-", "password"):
        assert secret_word not in raw