"""
QueryNest 统一数据模型。

定义整个系统共享的结构化类型：内容类型、文档元数据、检索上下文项、引用来源，
以及统一的 API 响应结构。这些类型是 Citation / Evaluation / Document Management
等功能可靠运行的基础。
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class ContentType(str, Enum):
    """内容类型 / 查询意图类型。"""

    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"
    EQUATION = "equation"
    MULTIMODAL = "multimodal"
    CROSS_DOCUMENT = "cross_document"
    GENERIC = "generic"

    @classmethod
    def has_value(cls, value: str) -> bool:
        try:
            cls(value)
            return True
        except ValueError:
            return False


@dataclass
class DocumentMetadata:
    """统一文档元数据；每个 chunk/image/table/equation 经由 document_id 关联到它。"""

    document_id: str
    filename: str
    file_type: str = ""
    page_count: int = 0
    created_at: str = ""
    parser: str = ""
    parse_method: str = ""
    content_types: List[str] = field(default_factory=list)
    source_path: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ContextItem:
    """单条检索上下文项，可表示 文本/图片/表格/公式 任一模态。"""

    type: str  # ContentType TEXT/IMAGE/TABLE/EQUATION/GENERIC
    content: str
    description: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str = ""          # 来源描述，如 "doc.pdf"
    page: int = 0
    score: float = 0.0
    document_id: str = ""
    chunk_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Citation:
    """结构化的引用来源，回答中通过 [N] 标注，用于 Citation 系统。"""

    document_id: str = ""
    document_name: str = ""
    page: int = 0
    content_type: str = ""      # text/image/table/equation
    chunk_id: str = ""
    source: str = ""            # 原始来源（文件路径或 URL）
    score: float = 0.0
    text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def display(self) -> str:
        """返回人类可读的引用标注，如 ``document.pdf — Page 4``。"""
        base = self.document_name or self.source or self.document_id
        labels = []
        if self.content_type and self.content_type != "text":
            labels.append(self.content_type.capitalize())
        if self.page and self.page > 0:
            labels.append(f"Page {self.page}")
        suffix = f" — {'; '.join(labels)}" if labels else ""
        return f"{base}{suffix}"


@dataclass
class RetrievalResult:
    """统一的查询/检索结果结构；也作为 FastAPI ``/query`` 的响应体。"""

    answer: str = ""
    sources: List[Citation] = field(default_factory=list)
    retrieval: Dict[str, Any] = field(default_factory=dict)   # 命中数、模式、耗时等
    metadata: Dict[str, Any] = field(default_factory=dict)    # 意图、是否改写等

    def to_dict(self) -> Dict[str, Any]:
        return {
            "answer": self.answer,
            "sources": [c.to_dict() for c in self.sources],
            "retrieval": self.retrieval,
            "metadata": self.metadata,
        }

    def markdown(self) -> str:
        """生成带引用标注的 Markdown 回答文本。"""
        body = self.answer or ""
        if self.sources:
            refs = "\n".join(
                f"[{i + 1}] {c.display()}" for i, c in enumerate(self.sources)
            )
            body = f"{body}\n\n**Sources:**\n{refs}"
        return body