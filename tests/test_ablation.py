"""Retrieval Ablation 单元测试（纯 Python，注入确定性检索器）。"""

import pytest

from querynest.evaluation.ablation import ablation_to_markdown, run_ablation
from querynest.evaluation.dataset import EvalExample


def _ex(question="q1", sources=None):
    return EvalExample(question=question, expected_sources=sources or ["paper.pdf"])


def _hit(text="x", doc="paper.pdf", cid="c1"):
    return {"document_name": doc, "content": text, "chunk_id": cid,
            "source": doc, "score": 0.9}


def _examples():
    return [
        _ex("q1", ["paper.pdf"]),
        _ex("q2", ["other.pdf"]),
    ]


def test_run_ablation_vector_relevant():
    # vector 检索器：对 q1 命中 paper.pdf（相关），对 q2 命中 paper.pdf（不相关）
    def vector(q):
        return [_hit(doc="paper.pdf", cid=f"v-{q}")]

    res = run_ablation({"vector": vector}, _examples(), ks=[5], top_k=5)
    row = res["strategies"]["vector"]
    assert row["status"] == "completed"
    # q1 相关命中 / 2 个相关 = 0.5（Recall@5）
    assert row["metrics"]["recall@5"] == 0.5
    # 2 条样例检索均返回 1 条；precision@5 = 命中数/5
    assert row["metrics"]["precision@5"] == 0.1
    assert row["metrics"]["mrr"] == 0.5
    assert 0.0 < row["metrics"]["ndcg"] <= 1.0
    # 真实计时：latency 应大于等于 0
    assert row["avg_latency_ms"] is not None


def test_run_ablation_latency_is_real_positive():
    """耗时必须来自真实计时（即便检索器立即返回也 > 0ms）。"""
    import time

    def slow(q):
        time.sleep(0.02)
        return [_hit(doc="paper.pdf")]

    res = run_ablation({"keyword": slow}, [_ex("q1", ["paper.pdf"])], ks=[5], top_k=5)
    ms = res["strategies"]["keyword"]["avg_latency_ms"]
    assert ms is not None and ms > 10.0


def test_run_ablation_not_available_strategy():
    res = run_ablation({"vector": lambda q: [_hit()]}, _examples(), ks=[5], top_k=5)
    assert res["strategies"]["hybrid_rerank"]["status"] == "not_available"
    assert "error" in res["strategies"]["hybrid_rerank"]


def test_run_ablation_failure_is_recorded():
    def broken(q):
        raise RuntimeError("embedding down")

    res = run_ablation({"hybrid": broken}, _examples(), ks=[5], top_k=5)
    row = res["strategies"]["hybrid"]
    assert row["status"] == "failed"
    assert "embedding down" in row["error"]
    assert row["metrics"] == {}


def test_run_ablation_empty_strategies_marks_all_not_available():
    res = run_ablation({}, _examples(), ks=[5], top_k=5)
    for name in ("vector", "keyword", "hybrid", "hybrid_rerank"):
        assert res["strategies"][name]["status"] == "not_available"


def test_run_ablation_unknown_strategy_raises():
    with pytest.raises(ValueError):
        run_ablation({"graphdb": lambda q: []}, _examples(), ks=[5], top_k=5)


def test_ablation_markdown_renders():
    res = run_ablation({"vector": lambda q: [_hit(doc="paper.pdf")]},
                       _examples(), ks=[5], top_k=5)
    md = ablation_to_markdown(res)
    assert "Strategy" in md
    assert "vector" in md
    assert "hybrid_rerank" in md
    assert "not_available" in md


def test_run_awaitable_async_and_sync():
    from querynest.core.engine import _run_awaitable

    async def coro(val=1):
        return val

    # 无运行循环：直接 asyncio.run
    assert _run_awaitable(coro(7)) == 7

    import asyncio

    # 有运行循环：走独立线程执行，不影响外层
    async def outer():
        return await _run_awaitable(coro(3))

    assert asyncio.run(outer()) == 3