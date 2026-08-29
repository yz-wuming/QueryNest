"""
Reranker 抽象与实现

新增的独立 Reranker 抽象。接口:

    rank = rerank(query, documents, top_k) -> list[(index, score)] 或
    rerank(query, documents, top_k) -> 重排后的文档序列

提供两个开箱即用的实现：
- ``BGEReranker``：封装 bge-reranker 类模型（FlagEmbedding / sentence-transformers
  CrossEncoder），模型库按需懒加载，未安装依赖时给出清晰提示。
- ``NoopReranker``：透传实现（关闭重排时使用）。

通过配置开关 ``enable_rerank`` 控制是否启用。
"""

import abc
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from querynest.core.exceptions import RerankError

DocumentLike = Union[str, Dict[str, Any]]


def _text_of(doc: DocumentLike) -> str:
    if isinstance(doc, str):
        return doc
    if isinstance(doc, dict):
        return doc.get("text") or doc.get("content") or doc.get("title") or str(doc)
    return str(doc)


class BaseReranker(abc.ABC):
    """Reranker 抽象基类。"""

    name: str = "base"

    def __init__(self, top_k_fallback: int = 10, **kwargs):
        self.top_k_fallback = top_k_fallback

    @abc.abstractmethod
    def rerank(
        self,
        query: str,
        documents: Sequence[DocumentLike],
        top_k: Optional[int] = None,
    ) -> List[Tuple[int, float]]:
        """返回按相关性降序的 ``(原始下标, 分数)`` 列表。"""
        raise NotImplementedError

    async def arerank(
        self,
        query: str,
        documents: Sequence[DocumentLike],
        top_k: Optional[int] = None,
    ) -> List[Tuple[int, float]]:
        """异步版本，默认在线程池中执行同步实现。"""
        import asyncio

        return await asyncio.get_running_loop().run_in_executor(
            None, self.rerank, query, documents, top_k
        )

    def reorder(self, query: str, documents: Sequence[DocumentLike], top_k: Optional[int] = None):
        """便捷：返回按分数重排后的文档序列（用分数标注）。"""
        pairs = self.rerank(query, documents, top_k)
        # 恢复到重排顺序，附带原下标
        return [(documents[i], score) for i, score in pairs]


class NoopReranker(BaseReranker):
    """透传 Reranker：不改变顺序，给全等分数。用于关闭重排。"""

    name = "noop"

    def rerank(self, query, documents, top_k=None):
        top_k = top_k or self.top_k_fallback
        k = min(len(documents), top_k)
        docs = list(documents)
        # 保持原顺序但按分数降序（相同分数），截断到 top_k
        result = [(i, self._score(i, len(docs))) for i in range(len(docs))]
        result = result[:k]
        return result

    @staticmethod
    def _score(idx: int, total: int) -> float:
        # 原顺序即可，分数仅用于排序稳定性
        return 0.0


class BGEReranker(BaseReranker):
    """基于 bge-reranker 风格的 Reranker。

    支持 FlagEmbedding 的 ``FlagReranker`` 与 sentence-transformers 的
    ``CrossEncoder``，二者 API 兼容（``predict(sentence_pairs)`` / ``predict(
    (sentence_pairs))``）。模型在首次调用时懒加载。
    """

    name = "bge-reranker"

    def __init__(self, model_name: str = "BAAI/bge-reranker-base", use_cpu: bool = True, **kwargs):
        super().__init__(**kwargs)
        self.model_name = model_name
        self.use_cpu = use_cpu
        self._model = None
        self._backend = None

    def _load(self):
        if self._model is not None:
            return
        try:
            from FlagEmbedding import FlagReranker

            self._model = FlagReranker(self.model_name, use_fp16=(not self.use_cpu))
            self._backend = "flag"
            return
        except Exception:  # noqa: BLE001
            pass
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name, device="cpu" if self.use_cpu else None
            )
            self._backend = "transformers"
            return
        except Exception:  # noqa: BLE001
            pass
        raise RerankError(
            "BGEReranker requires 'flagembedding' or 'sentence-transformers'. "
            f"Could not load reranker '{self.model_name}'. "
            "pip install flagembedding  (or sentence-transformers)."
        )

    def rerank(self, query, documents, top_k=None):
        self._load()
        if not documents:
            return []
        pairs = [[query, _text_of(d)] for d in documents]
        try:
            if self._backend == "flag":
                scores = self._model.compute_score(pairs, normalize=True)
            else:
                scores = self._model.predict(pairs)
        except Exception as e:  # noqa: BLE001
            raise RerankError(f"Rerank 推理失败: {e}") from e

        if isinstance(scores, (float, int)):
            scores = [float(scores)]
        scored = [(i, float(s)) for i, s in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)
        k = top_k or self.top_k_fallback
        return scored[:k]