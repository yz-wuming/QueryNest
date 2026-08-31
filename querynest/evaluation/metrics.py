"""
RAG Evaluation — 指标实现

确定性（可复现）指标：
- ``recall_at_k`` / ``precision_at_k`` / ``mrr``：基于 expected_sources 与检索命中的
  覆盖率/精确率/平均倒排秩，直接、可离线、可测试。

依赖外部判定器的指标（Faithfulness / Answer Relevancy）：
- 需要注入 ``judge_func``（LLM 裁判）或 ``embedding_func`` 才能计算；未注入时返回
  ``None`` 并计入 ``skipped``，绝不虚构数值。内置一个正则/词元重叠的
  ``lexical_faithfulness`` 作为便宜的启发式基线（文档中如实标注为启发式）。

评测测试集格式::

    {
      "question": "...",
      "expected_answer": "...",           # 可选
      "expected_sources": ["doc_a.pdf#4", "doc_b.pdf#2"]  # 或 [{"document":..., "page":...}]
    }
"""

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

StopWords = set(
    "the a an of for and or to in on at by with is are was were this that it its as from "
    "他 她 它 的 了 与 和 在 是 我 你 者 于 也 都 而 及 并 或 一 一张 一个 表示 模型 论文 "
    "这篇 这种 这个 那个 对 于 用 使用 关于 请问 如何 什么 为什么 比较 对比 哪个".split()
)


def _split_doc_page(raw: str) -> Tuple[str, int]:
    """把期望源字符串拆成 (document_key, page)。

    仅剥离明确的页码标记：``doc.pdf#4`` / ``doc.pdf# 4`` / ``doc.pdf — Page 4``。
    注意：不得按裸数字截断文件名本身（如 ``ph7_readme_kb.txt`` 应整体保留）。
    """
    s = (raw or "").strip()
    m = re.search(r"#\s*(\d+)\s*$", s)
    if m:
        return re.sub(r"#\s*\d+\s*$", "", s).strip(), int(m.group(1))
    m2 = re.search(r"(?i)(?:[-–—]\s*|:\s*)?Page\s*#?\s*(\d+)\s*$", s)
    if m2:
        return (
            re.sub(r"(?i)(?:[-–—]\s*|:\s*)?Page\s*#?\s*\d+\s*$", "", s).strip(),
            int(m2.group(1)),
        )
    return s, 0


def _norm_sources(expected_sources) -> List[Tuple[str, int]]:
    """把 expected_sources 归一化为 (document_key, page) 列表。"""
    out: List[Tuple[str, int]] = []
    for s in expected_sources or []:
        if isinstance(s, str):
            # 支持 "doc.pdf#4" / "doc.pdf — Page 4" 形式
            key, page = _split_doc_page(s)
        elif isinstance(s, dict):
            doc = str(s.get("document") or s.get("document_name") or s.get("source") or "")
            page = int(s.get("page") or 0)
            key = doc or str(s)
        else:
            key, page = str(s), 0
        if key:
            out.append((key, page))
    return out


def _hit_keys(hit: Any) -> Tuple[str, str]:
    """从单个检索命中提取 (document_key, page) 用于匹配。"""
    if isinstance(hit, dict):
        doc = (
            str(hit.get("document_name") or hit.get("document") or hit.get("source")
                or hit.get("file_path") or hit.get("document_id") or "")
        )
        try:
            page = int(hit.get("page") or hit.get("page_idx") or 0)
        except (TypeError, ValueError):
            page = 0
        return doc, str(page)
    return str(hit), ""


def recall_at_k(retrieved: Sequence[Any], expected_sources: Sequence[Any], k: int) -> float:
    """Recall@K：前 K 个命中覆盖了多少 expected source。"""
    expected = _norm_sources(expected_sources)
    if not expected:
        return 0.0
    hits = [_hit_keys(h) for h in list(retrieved)[:k]]
    covered = 0
    for doc, page in expected:
        matched = any(h_doc == doc for h_doc, _ in hits) or (
            page and any(h_doc == doc and h_page == str(page) for h_doc, h_page in hits)
        )
        if matched:
            covered += 1
    return covered / len(expected)


def precision_at_k(retrieved: Sequence[Any], expected_sources: Sequence[Any], k: int) -> float:
    """Precision@K：前 K 个命中中相关命中的比例。"""
    expected = {(d, p) for d, p in _norm_sources(expected_sources)}
    if not expected:
        return 0.0
    hits = list(retrieved)[:k]
    if not hits:
        return 0.0
    relevant = 0
    for hit in hits:
        doc, page = _hit_keys(hit)
        if (doc, page) in expected or any(d == doc for d, _ in expected):
            relevant += 1
    return relevant / k


def mrr(retrieved: Sequence[Any], expected_sources: Sequence[Any], k: int = 10) -> float:
    """MRR：首个相关命中的倒排秩均值（这里为单查询，返回该查询的 recip_rank）。"""
    expected = {(d, p) for d, p in _norm_sources(expected_sources)}
    if not expected:
        return 0.0
    for i, hit in enumerate(list(retrieved)[:k], start=1):
        doc, page = _hit_keys(hit)
        if (doc, page) in expected or any(d == doc for d, _ in expected):
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[Any], expected_sources: Sequence[Any], k: int = 10) -> float:
    """NDCG@K（将 expected_sources 全部视为相关，命中按 1 计）。"""
    import math

    expected = _norm_sources(expected_sources)
    if not expected:
        return 0.0
    hits = list(retrieved)[:k]
    if not hits:
        return 0.0

    def is_rel(hit) -> bool:
        doc, page = _hit_keys(hit)
        return (doc, page) in {(d, p) for d, p in expected} or any(
            d == doc for d, _ in expected
        )

    dcg = sum((1.0 if is_rel(h) else 0.0) / math.log2(i + 2) for i, h in enumerate(hits))
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(len(expected), len(hits))))
    if ideal == 0:
        return 0.0
    return dcg / ideal


def lexical_faithfulness(answer: str, context: str) -> float:
    """启发式 Faithfulness：答案与上下文的词元重叠率（仅为轻量基线，非 LLM 判定）。

    真值级的 Faithfulness 应使用 judge_func（见 FaithfulnessEvaluator）。
    """
    a_toks = _tokens(answer)
    c_toks = _tokens(context)
    if not a_toks:
        return 0.0
    if not c_toks:
        return 0.0
    overlap = len(a_toks & c_toks)
    return overlap / len(a_toks)


def _tokens(text: str) -> set:
    toks = re.findall(r"[A-Za-z]+|[\u4e00-\u9fff]", (text or "").lower())
    return {t for t in toks if t not in StopWords and len(t) > 1}


# ------------------------------------------------------------------ #
class FaithfulnessEvaluator:
    """Faithfulness 评估。

    - 注入 ``judge_func(context, answer) -> float[0,1]`` 时使用判定器。
    - 注入 ``embedding_func`` 时计算回答与上下文的余弦相似度（自包含、可计算）。
    - 都未注入时退化为 ``lexical_faithfulness`` 启发式，并标记 ``heuristic=True``。
    """

    def __init__(self, judge_func: Optional[Callable[[str, str], float]] = None,
                 embedding_func: Optional[Callable[[str], List[float]]] = None):
        self.judge_func = judge_func
        self.embedding_func = embedding_func

    def evaluate(self, context: str, answer: str) -> Dict[str, Any]:
        if self.judge_func is not None:
            try:
                s = float(self.judge_func(context, answer))
                return {"faithfulness": max(0.0, min(1.0, s)), "method": "judge"}
            except Exception:  # noqa: BLE001
                pass
        if self.embedding_func is not None:
            try:
                a = self.embedding_func(answer or "")
                c = self.embedding_func(context or "")
                if a and c:
                    s = _cosine(a, c)
                    return {"faithfulness": s, "method": "embedding"}
            except Exception:  # noqa: BLE001
                pass
        s = lexical_faithfulness(answer, context)
        return {"faithfulness": s, "method": "lexical_heuristic", "heuristic": True}


class AnswerRelevancyEvaluator:
    """Answer Relevancy（可选项）。

    注入 ``embedding_func`` 时，用 回答 与 问题 的相似度估算相关性；未注入时跳过。
    """

    def __init__(self, embedding_func: Optional[Callable[[str], List[float]]] = None):
        self.embedding_func = embedding_func

    def evaluate(self, question: str, answer: str) -> Dict[str, Any]:
        if self.embedding_func is None:
            return {"answer_relevancy": None, "skipped": True}
        try:
            a = self.embedding_func(answer or "")
            q = self.embedding_func(question or "")
            if a and q:
                return {
                    "answer_relevancy": float(_cosine(a, q)),
                    "skipped": False,
                    "method": "embedding",
                }
        except Exception:  # noqa: BLE001
            pass
        return {"answer_relevancy": None, "skipped": True}


def _cosine(a: List[float], b: List[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)