"""
Multimodal Context Builder

把检索命中的 文本 / 图片 / 表格 / 公式 统一转换为模型（LLM/VLM）可理解的
结构化上下文 ``ContextItem`` 序列，并支持渲染为拼装好的文本（供 Prompt 注入）。

每个 ContextItem 都携带来源元数据（document_id / page / content_type /
chunk_id / source / score），从而让 Citation 与 Evaluation 可靠工作。
"""

from typing import Any, Dict, Iterable, List, Optional, Union

from querynest.core.models import ContentType, ContextItem
from querynest.core.exceptions import QueryError

CONTENT_KEYS = {
    ContentType.TEXT: ("text", "content", "text_content"),
    ContentType.IMAGE: ("img_path", "image_path", "file_path", "image_url"),
    ContentType.TABLE: ("table_body", "table_data", "content"),
    ContentType.EQUATION: ("latex", "text", "equation_text"),
}

SIMPLE_CONTENT_KEYS = ("text", "content", "table_body", "table_data", "latex", "img_path",
                       "image_path", "file_path", "img_caption", "image_caption")


class ContextBuilder:
    """把原始命中聚合成结构化的 ContextItem 列表，并可渲染为文本。"""

    def __init__(self, max_items: int = 20, include_header: bool = True):
        self.max_items = max_items
        self.include_header = include_header

    def build(self, documents: Iterable[Any]) -> List[ContextItem]:
        """将任意检索命中（dict / str / ContextItem）转成 ContextItem 列表。"""
        items: List[ContextItem] = []
        strict = ContentType.has_value

        for doc in documents:
            if isinstance(doc, ContextItem):
                items.append(doc)
                continue
            if isinstance(doc, str):
                items.append(ContextItem(type=ContentType.TEXT, content=doc))
                continue
            if isinstance(doc, dict):
                items.append(self._from_dict(doc))
                continue
            try:
                items.append(self._from_object(doc))
            except Exception as e:  # noqa: BLE001
                raise QueryError(f"无法构造上下文项: {e}", context={"item": doc})

        # 按 score 降序 + 截断
        items.sort(key=lambda i: i.score, reverse=True)
        return items[: self.max_items]

    def render(self, items: List[ContextItem]) -> str:
        """把 ContextItem 列表渲染为单一文本块（供 Prompt/LLM 注入）。"""
        if not items:
            return ""
        parts = []
        if self.include_header:
            parts.append(f"# Retrieval Context ({len(items)} items)")
        for i, item in enumerate(items, 1):
            header = f"\n[{i}] type={item.type} page={item.page}"
            if item.source:
                header += f" source={item.source}"
            if item.score:
                header += f" score={item.score:.3f}"
            desc = f"\n    description: {item.description}" if item.description else ""
            body = f"\n    content: {item.content}" if item.content else ""
            parts.append(f"{header}{desc}{body}")
        return "\n".join(parts)

    # ------------------------------------------------------------------ #
    def _from_dict(self, hit: Dict[str, Any]) -> ContextItem:
        ctype = str(hit.get("type") or hit.get("content_type") or ContentType.TEXT.value)
        content_types = ContentType
        if not content_types.has_value(ctype):
            ctype = ContentType.TEXT.value

        if ctype == ContentType.MULTIMODAL.value:
            ctype = ContentType.TEXT.value

        content = self._extract_content(hit, ctype)
        description = str(
            hit.get("description") or hit.get("enhanced_caption") or ""
        )
        source = str(hit.get("source") or hit.get("file_path") or hit.get("path") or "")
        document_id = str(hit.get("document_id") or hit.get("doc_id") or "")
        chunk_id = str(hit.get("chunk_id") or hit.get("chunk") or "")
        try:
            page = int(hit.get("page") or hit.get("page_idx") or 0)
        except (TypeError, ValueError):
            page = 0
        try:
            score = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0

        return ContextItem(
            type=ctype,
            content=content,
            description=description,
            metadata=dict(hit),
            source=source,
            page=page,
            score=score,
            document_id=document_id,
            chunk_id=chunk_id,
        )

    @staticmethod
    def _extract_content(hit: Dict[str, Any], ctype: str) -> str:
        keys = CONTENT_KEYS.get(ContentType(ctype), SIMPLE_CONTENT_KEYS)
        for k in keys:
            if k in hit and hit[k] is not None:
                v = hit[k]
                if isinstance(v, (list, tuple)):
                    return "\n".join(str(x) for x in v if str(x).strip())
                if isinstance(v, dict):
                    return str(v)
                return str(v)
        return ""

    def _from_object(self, obj: Any) -> ContextItem:
        def g(*names, default=""):
            for n in names:
                if hasattr(obj, n):
                    return getattr(obj, n)
            return default

        ctype = str(g("type", "content_type", default=ContentType.TEXT.value))
        return ContextItem(
            type=ctype if ContentType.has_value(ctype) else ContentType.TEXT.value,
            content=str(g("content", "text", "description")),
            description=str(g("description", "enhanced_caption", "")),
            source=str(g("source", "file_path", "path")),
            page=int(_safe_int(g("page", "page_idx", default=None))),
            score=float(g("score") or 0.0),
            document_id=str(g("document_id", "doc_id")),
            chunk_id=str(g("chunk_id", "chunk")),
        )


def _safe_int(v):
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0