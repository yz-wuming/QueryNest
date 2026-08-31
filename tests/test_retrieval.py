"""Hybrid Retrieval / Reranker / BM25 / Context Builder tests (pure Python)."""

from querynest.retrieval.context import ContextBuilder
from querynest.retrieval.hybrid import FunctionRetriever, HybridRetriever
from querynest.retrieval.keyword import BM25Retriever
from querynest.retrieval.reranker import NoopReranker, BaseReranker, _text_of


def _h(text, cid):
    return {"document_name": "doc", "content": text, "chunk_id": cid,
            "document_id": "d1", "page": 1, "type": "text"}


# ---------------- Hybrid ----------------
def test_hybrid_rrf_fuses_routes():
    dense = FunctionRetriever(lambda q: [_h("alpha", "a1"), _h("beta", "b1")], "dense")
    graph = FunctionRetriever(lambda q: [_h("beta", "b1"), _h("gamma", "c1")], "graph")
    hr = HybridRetriever(dense=dense, graph=graph)
    hits = hr.retrieve("q", top_k=5)
    # beta 在两条路都命中，应靠前且去重后合并为一条
    texts = [h["content"] for h in hits]
    assert len(hits) == len(set(h["chunk_id"] for h in hits))
    assert "beta" in texts


def test_hybrid_dedupes():
    dense = FunctionRetriever(lambda q: [_h("same", "s1")], "dense")
    graph = FunctionRetriever(lambda q: [_h("same", "s1")], "graph")
    hr = HybridRetriever(dense=dense, graph=graph)
    assert len(hr.retrieve("q")) == 1


def test_hybrid_empty_routes():
    assert HybridRetriever().retrieve("q") == []


def test_hybrid_rrf_score_present():
    dense = FunctionRetriever(lambda q: [_h("x", "x1")], "dense")
    hr = HybridRetriever(dense=dense)
    hits = hr.retrieve("q")
    assert hits[0].get("rrf_score", 0) > 0
    assert hits[0]["fusion"] == "rrf"


# ---------------- Reranker ----------------
def test_noop_reranker_returns_indices():
    docs = [_h("a", "1"), _h("b", "2")]
    pairs = NoopReranker().rerank("q", docs, top_k=2)
    assert pairs == [(0, 0.0), (1, 0.0)]


def test_base_reranker_is_abstract():
    import pytest

    with pytest.raises(TypeError):
        BaseReranker()  # abstract


def test_text_of():
    assert _text_of("hello") == "hello"
    assert _text_of({"content": "c"}) == "c"
    assert _text_of({"text": "t"}) == "t"


def test_noop_reorder():
    docs = ["a", "b", "c"]
    ordering = NoopReranker().reorder("q", docs, top_k=2)
    assert len(ordering) == 2
    assert [d for d, _ in ordering] == ["a", "b"]


# ---------------- BM25 ----------------
def test_bm25_relevant_first():
    corpus = [
        {"document_id": "d1", "text": "深度学习模型在大规模数据集上表现良好"},
        {"document_id": "d2", "text": "表格解析用于提取结构化数据"},
    ]
    r = BM25Retriever(corpus)
    hits = r.retrieve("深度学习模型", top_k=1)
    assert hits[0]["document_id"] == "d1"


def test_bm25_empty_corpus():
    assert BM25Retriever([]).retrieve("anything", 5) == []


def test_bm25_english():
    corpus = [{"document_id": "a", "text": "retrieval augmented generation for documents"},
              {"document_id": "b", "text": "image captioning with vision"}]
    r = BM25Retriever(corpus)
    hits = r.retrieve("retrieval augmented", 1)
    assert hits[0]["document_id"] == "a"


# ---------------- Context Builder ----------------
def test_context_builder_from_hits():
    cb = ContextBuilder()
    items = cb.build([_h("hello table body", "c1")])
    assert len(items) == 1
    assert items[0].type == "text"
    assert items[0].content == "hello table body"


def test_context_builder_string():
    cb = ContextBuilder()
    items = cb.build(["plain string"])
    assert items[0].content == "plain string"


def test_context_builder_render():
    cb = ContextBuilder()
    items = cb.build([_h("hello", "c1")])
    out = cb.render(items)
    assert "hello" in out
    assert "Retrieval Context" in out


def test_context_builder_preserves_metadata():
    cb = ContextBuilder()
    hit = _h("x", "c1")
    hit.update({"document_id": "doc-9", "page": 3})
    items = cb.build([hit])
    assert items[0].document_id == "doc-9"
    assert items[0].page == 3