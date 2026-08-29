"""
Keyword / BM25 检索器（纯 Python，无第三方依赖）

为 Hybrid Retrieval 提供第三条召回路：在给定文档语料上做 BM25 打分，
不依赖任何索引/向量数据库。语料可由 DocumentStore 的已存来源文本提供，
也可在用例中直接注入。

可独立测试，不依赖 LightRAG / MinerU。
"""

import math
import re
import threading
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence

from querynest.core.exceptions import RetrievalError
from querynest.retrieval.hybrid import BaseRetriever


_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    """中英混排分词：英文按词、中文按单字并组合去重。"""
    if not text:
        return []
    tokens = [t.lower() for t in _WORD_RE.findall(text)]
    # 中文：把连续中文字符切成 2-gram，提升召回
    out: List[str] = []
    cn_run = ""
    for t in _WORD_RE.findall(text):
        if re.fullmatch(r"[\u4e00-\u9fff]+", t):
            cn_run += t
        else:
            if len(cn_run) >= 2:
                out.extend(_bigrams(cn_run))
            elif cn_run:
                out.append(cn_run.lower())
            cn_run = ""
            out.append(t.lower())
    if len(cn_run) >= 2:
        out.extend(_bigrams(cn_run))
    elif cn_run:
        out.append(cn_run.lower())
    return out


def _bigrams(text: str) -> List[str]:
    return [text[i : i + 2] for i in range(len(text) - 1)]


class BM25Retriever(BaseRetriever):
    """在内存语料上实现的 BM25（经典参数 k1=1.5, b=0.75）。

    Hits 供入（dict）：``{"document_id", "text"|"content", "document_name"?, "page"?}``
    """

    K1 = 1.5
    B = 0.75

    def __init__(
        self,
        corpus: Optional[Sequence[Dict[str, Any]]] = None,
        name: str = "bm25",
        top_k_default: int = 20,
    ):
        self.name = name
        self.top_k_default = top_k_default
        self._lock = threading.RLock()
        # (text, doc_fields, term_freqs)
        self._docs: List[tuple] = []
        self._df: Dict[str, int] = {}
        self._avgdl = 0.0
        self._n = 0
        if corpus:
            self.set_corpus(corpus)

    # ------------------------------------------------------------ #
    def set_corpus(self, corpus: Sequence[Dict[str, Any]]) -> None:
        """重建索引。corpus[i] 需含 ``document_id`` 与 ``text``/``content``。"""
        with self._lock:
            self._docs = []
            self._df = {}
            doc_freq: Dict[str, set] = {}
            total_len = 0
            for doc in corpus:
                text = doc.get("text") or doc.get("content") or ""
                if not text:
                    continue
                terms = set(_tokenize(text))
                freqs = Counter(_tokenize(text))
                self._docs.append((text, doc, dict(freqs)))
                total_len += sum(freqs.values())
                for term in terms:
                    doc_freq.setdefault(term, set()).add(len(self._docs) - 1)
            self._n = len(self._docs)
            self._avgdl = total_len / self._n if self._n else 0.0
            for term, ids in doc_freq.items():
                self._df[term] = len(ids)

    def add_documents(self, docs: Sequence[Dict[str, Any]]) -> None:
        """增量追加文档（与 set_corpus 等价，方便上层持续更新）。"""
        self.set_corpus(list(self._iter_docs()) + list(docs))

    def _iter_docs(self):
        for _, fields, _ in self._docs:
            yield fields

    # ------------------------------------------------------------ #
    def retrieve(self, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        k = top_k or self.top_k_default
        if self._n == 0:
            return []
        q_terms = set(_tokenize(query))
        if not q_terms:
            return []
        idf = {t: self._idf(t) for t in q_terms}
        scored: Dict[int, float] = {}
        with self._lock:
            for idx, (text, fields, freqs) in enumerate(self._docs):
                length = sum(freqs.values()) or 1
                score = 0.0
                for term in q_terms:
                    tf = freqs.get(term, 0)
                    if tf == 0 or term not in self._df:
                        continue
                    denom = tf + self.K1 * (1 - self.B + self.B * length / (self._avgdl or 1))
                    score += idf[term] * (tf * (self.K1 + 1)) / denom
                if score > 0:
                    scored[idx] = score
        ranked = sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:k]
        out = []
        with self._lock:
            for idx, score in ranked:
                _, fields, _ = self._docs[idx]
                hit = dict(fields)
                hit["score"] = score
                hit["content"] = hit.get("text") or hit.get("content") or ""
                hit["content_type"] = "text"
                hit["chunk_id"] = hit.get("chunk_id") or f"bm25:{idx}"
                out.append(hit)
        return out

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        return math.log(1 + (self._n - df + 0.5) / (df + 0.5))


def build_corpus_texts(docs: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把 DocumentStore 列表转换成 BM25 语料条目。"""
    corpus = []
    for d in docs:
        body = _read_source(d)
        corpus.append(
            {
                "document_id": d.get("document_id") or d.get("id"),
                "document_name": d.get("filename") or d.get("document_id"),
                "text": body,
                "source_path": d.get("source_path", ""),
                "page": d.get("page_count", 0),
            }
        )
    return corpus


# 这些扩展名的"来源"是二进制，绝不能 read_text() 塞进 BM25 语料。
_BINARY_SOURCE_EXTS = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff",
    ".xlsx", ".xls", ".docx", ".doc", ".pptx", ".ppt",
    ".zip", ".gz", ".7z", ".tar", ".bin", ".dat", ".pickle", ".pkl",
}


def _read_source(doc: Dict[str, Any]) -> str:
    """读取文档已保存的来源正文；二进制来源或读取失败返回空串而不是抛错。"""
    path = doc.get("source_path", "")
    if not path:
        return ""
    try:
        from pathlib import Path

        p = Path(path)
        if p.exists() and p.is_file():
            if p.suffix.lower() in _BINARY_SOURCE_EXTS:
                return ""
            return p.read_text(encoding="utf-8", errors="ignore")
    except OSError:  # noqa: BLE001
        return ""
    return ""