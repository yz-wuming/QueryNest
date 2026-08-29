"""
Document Management — 知识库文档管理

提供 list / get / delete / exists / status 等真正的知识库管理能力。

实现：轻量 JSON manifest 索引 + 磁盘目录（``storage_dir``），文档条目以
``DocumentMetadata`` 存取。可与上层引擎（QueryNest）配合，在解析后将元数据写入
此仓库，从而支持多文档管理、检索引用定位、评测来源标注。
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from querynest.core.exceptions import DocumentNotFoundError
from querynest.core.models import DocumentMetadata


class DocumentStore:
    """线程安全的文档仓库（JSON 索引 + 磁盘文档目录）。"""

    MANIFEST = "documents.json"

    def __init__(self, storage_dir: str = "./querynest_storage"):
        self.storage_dir = Path(storage_dir)
        self.docs_dir = self.storage_dir / "documents"
        self.manifest_path = self.storage_dir / self.MANIFEST
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------- #
    def upsert(self, metadata: DocumentMetadata, content: Optional[str] = None,
               source_text: Optional[str] = None) -> DocumentMetadata:
        """新增或更新一条文档元数据；可选地把正文/来源文本落到磁盘。"""
        with self._lock:
            now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            if not metadata.created_at:
                metadata.created_at = now
            if content:
                self._save_source(metadata.document_id, content)
            # 保留旧条目的内容路径
            prev = self._index.get(metadata.document_id, {})
            if prev and not metadata.source_path:
                metadata.source_path = prev.get("source_path", "")
            self._index[metadata.document_id] = metadata.to_dict()
            self._flush()
            return metadata

    def list_documents(self, limit: int = 1000) -> List[Dict[str, Any]]:
        with self._lock:
            rows = list(self._index.values())
        rows.sort(key=lambda r: r.get("created_at", ""))
        return rows[:limit]

    def get_document(self, document_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._index.get(document_id)
        if not row:
            raise DocumentNotFoundError(f"文档不存在: {document_id}", context={"document_id": document_id})
        return row

    def get_metadata(self, document_id: str) -> DocumentMetadata:
        return DocumentMetadata(**self.get_document(document_id))

    def document_exists(self, document_id: str) -> bool:
        with self._lock:
            return document_id in self._index

    def document_status(self, document_id: str) -> str:
        with self._lock:
            row = self._index.get(document_id)
        if not row:
            raise DocumentNotFoundError(f"文档不存在: {document_id}", context={"document_id": document_id})
        return row.get("status", "ready")

    def delete_document(self, document_id: str) -> bool:
        with self._lock:
            if document_id not in self._index:
                return False
            del self._index[document_id]
            # 清除磁盘上的来源文本
            src = self.docs_dir / f"{document_id}.txt"
            if src.exists():
                try:
                    src.unlink()
                except OSError:
                    pass
            self._flush()
            return True

    def read_source(self, document_id: str) -> str:
        """读取已保存的文档来源文本（若存在）。"""
        src = self.docs_dir / f"{document_id}.txt"
        if src.exists():
            return src.read_text(encoding="utf-8")
        return ""

    # ------------------------------------------------------------- #
    def _save_source(self, document_id: str, content: str) -> None:
        path = self.docs_dir / f"{document_id}.txt"
        # doc_id 可能含路径分隔符（如 "examples/data/sample.pdf"），确保父目录存在
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        record = self._index.get(document_id, {})
        record["source_path"] = str(path)
        record["status"] = "processed"

    def _load(self) -> None:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self._index = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _flush(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.manifest_path)