"""
Citation 系统（QueryNest 核心产品功能之一）

把检索阶段返回的原始命中（可能来自不同存储层、字段名不统一）统一归一化为
结构化的 ``Citation`` 对象，并完成去重、排序与编号，使其可被回答中的 ``[N]``
标注可靠引用。

关键点：引用信息必须在检索阶段就随命中携带（document_id / page / content_type /
chunk_id / source），而不是在 Prompt 里要求模型编造。
"""

from typing import Any, Dict, Iterable, List, Optional, Union

from querynest.core.exceptions import CitationError
from querynest.core.models import Citation, ContentType, ContextItem


class CitationBuilder:
    """把检索命中聚合成规整、去重、排序的引用列表。"""

    def __init__(self, max_sources: int = 10, dedupe_by: str = "chunk"):
        self.max_sources = max_sources
        # dedupe_by: "chunk"(chunk_id) | "doc_page"(document_id+page) | "document_id"
        self.dedupe_by = dedupe_by

    def build(self, hits: Iterable[Any]) -> List[Citation]:
        """把原始命中（dict / ContextItem / 对象）转成排序后的 Citation 列表。"""
        citations: List[Citation] = []
        seen = set()

        for hit in hits:
            item = self._coerce(hit)
            if item is None:
                continue
            key = self._dedupe_key(item)
            if key in seen:
                continue
            seen.add(key)
            citations.append(item)

        # 按相关性降序
        citations.sort(key=lambda c: c.score, reverse=True)
        # 去掉为空的占位
        citations = [c for c in citations if c.document_name or c.document_id or c.source]
        return citations[: self.max_sources]

    # ------------------------------------------------------------------ #
    @classmethod
    def _coerce(cls, hit: Any) -> Optional[Citation]:
        if isinstance(hit, Citation):
            return hit
        if isinstance(hit, ContextItem):
            return Citation(
                document_id=hit.document_id,
                document_name=hit.source,
                page=hit.page,
                content_type=hit.type,
                chunk_id=hit.chunk_id,
                source=hit.source,
                score=hit.score,
                text=hit.content,
            )
        if isinstance(hit, dict):
            return cls._from_dict(hit)
        # 兼容任意对象
        return cls._from_object(hit)

    @staticmethod
    def _from_dict(hit: Dict[str, Any]) -> Optional[Citation]:
        try:
            document_id = (
                hit.get("document_id")
                or hit.get("doc_id")
                or hit.get("id")
                or ""
            )
            document_name = (
                hit.get("document_name")
                or hit.get("document")
                or hit.get("filename")
                or hit.get("file_name")
                or ""
            )
            page = hit.get("page") or hit.get("page_idx") or hit.get("page_num") or 0
            content_type = (
                hit.get("content_type")
                or hit.get("type")
                or hit.get("modal_type")
                or ""
            )
            chunk_id = (
                hit.get("chunk_id")
                or hit.get("chunk")
                or hit.get("ckt")
                or hit.get("id")
                or ""
            )
            source = hit.get("source") or hit.get("file_path") or hit.get("path") or ""
            score = float(hit.get("score") or hit.get("distance") or 0.0 or 0.0)
            text = hit.get("text") or hit.get("content") or ""

            # 类型归一：只保留我们定义过的类型；未知归为 text
            if not ContentType.has_value(str(content_type)):
                content_type = ""
            return Citation(
                document_id=str(document_id),
                document_name=str(document_name),
                page=_safe_int(page),
                content_type=str(content_type),
                chunk_id=str(chunk_id),
                source=str(source),
                score=score,
                text=str(text),
            )
        except Exception as e:  # noqa: BLE001
            raise CitationError(f"无法解析引用来源: {e}", context={"hit": hit})

    @staticmethod
    def _from_object(hit: Any) -> Optional[Citation]:
        def g(*names):
            for n in names:
                if hasattr(hit, n):
                    return getattr(hit, n)
            return None

        try:
            return Citation(
                document_id=str(g("document_id", "doc_id", "id") or ""),
                document_name=str(g("document_name", "document", "filename") or ""),
                page=_safe_int(g("page", "page_idx", "page_num")),
                content_type=str(g("content_type", "type") or ""),
                chunk_id=str(g("chunk_id", "chunk", "id") or ""),
                source=str(g("source", "file_path", "path") or ""),
                score=float(g("score") or 0.0),
                text=str(g("text", "content") or ""),
            )
        except Exception:  # noqa: BLE001
            return None

    def _dedupe_key(self, c: Citation) -> str:
        if self.dedupe_by == "chunk" and c.chunk_id:
            return f"chunk:{c.chunk_id}"
        if self.dedupe_by == "doc_page" and (c.document_id or c.document_name) and c.page:
            return f"docpage:{c.document_id or c.document_name}:{c.page}"
        if self.dedupe_by == "document_id" and (c.document_id or c.document_name):
            return f"doc:{c.document_id or c.document_name}"
        # 兜底：source+page+type
        return f"src:{c.document_name or c.source}:{c.page}:{c.content_type}"


def _safe_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0