"""
Hybrid Retrieval 编排

不依赖单一检索路：Dense(向量) + Keyword/BM25 + Graph(知识图) 多路召回后，
经 候选融合(RRF/加权) → 去重 → Rerank → 输出最终上下文。

设计为「检索器即回调」的编排层，底层实际检索可由 LightRAG 向量/图、BM25 或任意
回调提供；QueryNest 在此之上负责多路融合与重排，不重复实现底层数据库。

hit 约定（dict）：``{"text"|"content", "score", "document_id", "document_name",
"source", "page", "type", "chunk_id"}``
"""

import abc
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from querynest.core.exceptions import RetrievalError
from querynest.query.citation import CitationBuilder
from querynest.retrieval.context import ContextBuilder

# 检索器回调：query -> List[Dict]; 亦接受带 .retrieve/.__call__ 的对象
RetrieverLike = Callable[[str], List[Dict[str, Any]]]


class BaseRetriever(abc.ABC):
    @abc.abstractmethod
    def retrieve(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        raise NotImplementedError

    async def retrieve_async(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        """默认通过线程池执行同步实现；异步实现可覆盖。"""
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(
            None, self.retrieve, query, top_k
        )


class FunctionRetriever(BaseRetriever):
    """把一个简单回调包装为 BaseRetriever。"""

    def __init__(self, fn: RetrieverLike, name: str = "fn", is_async: bool = False):
        self.fn = fn
        self.name = name
        self.is_async = is_async

    def retrieve(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        hits = self.fn(query)
        return list(hits)[:top_k] if top_k else list(hits)

    async def retrieve_async(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        if self.is_async:
            result = await self.fn(query)
            hits = result or []
        else:
            hits = self.fn(query)
        return list(hits)[:top_k] if top_k else list(hits)


class HybridRetriever(BaseRetriever):
    """多路召回融合检索器。"""

    RRF_K = 60.0

    def __init__(
        self,
        dense: Optional[BaseRetriever] = None,
        keyword: Optional[BaseRetriever] = None,
        graph: Optional[BaseRetriever] = None,
        reranker: Optional[Any] = None,
        fusion: str = "rrf",        # "rrf" | "score"
        weights: Optional[Dict[str, float]] = None,
        dense_weight: float = 0.4,
        keyword_weight: float = 0.3,
        graph_weight: float = 0.3,
        enable_rerank: bool = False,
        rerank_top_k: int = 10,
        context_builder: Optional[ContextBuilder] = None,
        citation_builder: Optional[CitationBuilder] = None,
    ):
        # 记录启用的路由
        self.routes: Dict[str, BaseRetriever] = {}
        for name, r in (("dense", dense), ("keyword", keyword), ("graph", graph)):
            if r is not None:
                self.routes[name] = r
        self.reranker = reranker
        self.fusion = fusion
        self.weights = weights or {
            "dense": dense_weight,
            "keyword": keyword_weight,
            "graph": graph_weight,
        }
        self.enable_rerank = enable_rerank
        self.rerank_top_k = rerank_top_k
        self.context_builder = context_builder or ContextBuilder()
        self.citation_builder = citation_builder or CitationBuilder()

    def retrieve(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        if not self.routes:
            return []
        per_route: Dict[str, List[Dict[str, Any]]] = {}
        try:
            for name, retriever in self.routes.items():
                per_route[name] = retriever.retrieve(query, top_k=max(top_k, 20))
        except Exception as e:  # noqa: BLE001
            raise RetrievalError(f"混合检索失败({e.__class__.__name__}): {e}")

        # 1) 融合
        if self.fusion == "rrf":
            fused = self._rrf_fuse(per_route)
        elif self.fusion == "score":
            fused = self._score_fuse(per_route)
        else:
            raise RetrievalError(f"未知融合策略: {self.fusion}")

        # 2) 去重
        fused = self._dedupe(fused)

        # 3) 重排
        if self.enable_rerank and self.reranker is not None and fused:
            fused = self._rerank(query, fused)

        return fused[: max(top_k, 1)]

    async def retrieve_async(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        if not self.routes:
            return []
        per_route: Dict[str, List[Dict[str, Any]]] = {}
        try:
            for name, retriever in self.routes.items():
                if hasattr(retriever, "retrieve_async"):
                    per_route[name] = await retriever.retrieve_async(query, top_k=max(top_k, 20))
                else:
                    per_route[name] = retriever.retrieve(query, top_k=max(top_k, 20))
        except Exception as e:  # noqa: BLE001
            raise RetrievalError(f"混合检索失败({e.__class__.__name__}): {e}")

        if self.fusion == "rrf":
            fused = self._rrf_fuse(per_route)
        elif self.fusion == "score":
            fused = self._score_fuse(per_route)
        else:
            raise RetrievalError(f"未知融合策略: {self.fusion}")

        fused = self._dedupe(fused)
        if self.enable_rerank and self.reranker is not None and fused and hasattr(self.reranker, "arerank"):
            fused = self._rerank_async(query, fused)
        elif self.enable_rerank and self.reranker is not None and fused:
            fused = self._rerank(query, fused)

        return fused[: max(top_k, 1)]

    def build_context(self, hits: Sequence[Dict[str, Any]]):
        """把命中转成 ContextItem 列表（供注入）。"""
        return self.context_builder.build(hits)

    def build_citations(self, hits: Sequence[Dict[str, Any]]):
        """把命中转成规整的 Citation 列表。"""
        return self.citation_builder.build(hits)

    # ------------------------------------------------------------------ #
    def _rrf_fuse(self, per_route: Dict[str, List]) -> List[Dict]:
        acc: Dict[str, Dict] = {}
        order: List[str] = []
        for name, hits in per_route.items():
            for rank, h in enumerate(hits):
                key = _hit_key(h)
                if key not in acc:
                    acc[key] = {"hit": h, "rrf": 0.0, "routes": []}
                    order.append(key)
                entry = acc[key]
                w = self.weights.get(name, 1.0)
                entry["rrf"] += w * 1.0 / (self.RRF_K + rank + 1)
                entry["routes"].append(name)
                if not entry["hit"].get("score") and h.get("score"):
                    entry["hit"]["score"] = h.get("score")
        fused = []
        for key in order:
            entry = acc[key]
            h = dict(entry["hit"])
            h["rrf_score"] = entry["rrf"]
            # 融合分数：主要取 RRF（打分主流），保留原始单路分数作参考
            h["score"] = entry["rrf"]
            h["fusion"] = self.fusion
            h["hit_routes"] = entry["routes"]
            fused.append(h)
        fused.sort(key=lambda x: x["score"], reverse=True)
        return fused

    def _score_fuse(self, per_route: Dict[str, List]) -> List[Dict]:
        merged: Dict[str, List[Tuple[str, float]]] = {}
        hits_by_key: Dict[str, Dict] = {}
        order: List[str] = []
        for name, hits in per_route.items():
            w = self.weights.get(name, 1.0)
            # 归一化到 [0,1]
            if hits:
                mx = max(float(h.get("score") or 0.0) for h in hits)
            else:
                mx = 1.0
            for h in hits:
                key = _hit_key(h)
                score = (float(h.get("score") or 0.0) / mx if mx else 0.0) * w
                merged.setdefault(key, []).append((name, score))
                hits_by_key.setdefault(key, h)
                if key not in order:
                    order.append(key)
        fused = []
        for key in order:
            total = sum(s for _, s in merged[key])
            h = dict(hits_by_key[key])
            h["score"] = total
            h["fusion"] = self.fusion
            h["hit_routes"] = [n for n, _ in merged[key]]
            fused.append(h)
        fused.sort(key=lambda x: x["score"], reverse=True)
        return fused

    @staticmethod
    def _dedupe(hits: List[Dict]) -> List[Dict]:
        seen = set()
        out = []
        for h in hits:
            key = _hit_key(h)
            if key in seen:
                continue
            seen.add(key)
            out.append(h)
        return out

    def _rerank(self, query: str, hits: List[Dict]) -> List[Dict]:
        pairs = self.reranker.rerank(query, hits, top_k=min(len(hits), self.rerank_top_k * 3))
        scored = []
        for idx, score in pairs:
            h = dict(hits[idx])
            h["score"] = float(score)
            h["reranked"] = True
            scored.append(h)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored  # 已按 top_k 截断

    async def _rerank_async(self, query: str, hits: List[Dict]) -> List[Dict]:
        arerank = getattr(self.reranker, "arerank", None)
        if arerank is None:
            return self._rerank(query, hits)
        pairs = await arerank(query, hits, top_k=min(len(hits), self.rerank_top_k * 3))
        scored = []
        for idx, score in pairs:
            h = dict(hits[idx])
            h["score"] = float(score)
            h["reranked"] = True
            scored.append(h)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored


def _hit_key(h: Dict[str, Any]) -> str:
    chunk = h.get("chunk_id") or h.get("id")
    src = h.get("source") or h.get("document_name") or h.get("file_path") or ""
    text = str(h.get("text") or h.get("content") or "")[:80]
    page = h.get("page") or h.get("page_idx") or 0
    if chunk:
        return f"chunk:{chunk}"
    return f"src:{src}:{page}:{text}"