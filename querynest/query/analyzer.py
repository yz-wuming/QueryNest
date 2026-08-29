"""
Query Analyzer

负责判断用户问题的类型：TEXT / IMAGE / TABLE / EQUATION / MULTIMODAL /
CROSS_DOCUMENT。

实现方式：
- 默认走确定性规则引擎（正则 + 关键词计分），不依赖 LLM，保证可离线、可测试。
- 可选地注入 ``llm_func`` 或 ``classify_func`` 做更细粒度的语义分类（例如借助轻量
  模型），规则引擎未命中或需要置信度时启用。
"""

import re
from typing import Any, Callable, Dict, List, Optional

from querynest.core.models import ContentType

# 与 ContentType 对齐的语义别称
QueryIntent = ContentType


# 规则：每个意图对应一组「关键词/正则」及权重
_LEXICON: Dict[ContentType, List[str]] = {
    ContentType.TABLE: [
        r"表[格\d]|表格|表中的|table|本表|下图表格|哪(个|一行|一列)|统计表|数据表",
        r"比(较|对)|效果最好|准确率|precision|recall|acc\b",
    ],
    ContentType.IMAGE: [
        r"架构图|示意图|流程图|结构图|图片|图像|图形|示意图",
        r"\bfigure\b|\bimage\b|\bdiagram\b|\bchart\b|\bplot\b|picture",
        r"这张图|该图|图中|图示|这幅图",
    ],
    ContentType.EQUATION: [
        r"公式|等式|推导|方程|latex|数学表达式|算子|加(权|和)公式",
        r"\bequation\b|\bformula\b|\bderiv(ation|ative)?s?\b",
    ],
    ContentType.CROSS_DOCUMENT: [
        r"对比\s*(这几?篇|多篇|这些|两篇|三篇)|比较.*(篇|文档|paper)",
        r"这几?篇|多篇文档|跨文档|综述|总结.*(篇|文档)",
        r"between (the|these|\d+) (papers|documents)",
        r"compare|comparison|across documents|difference between",
    ],
    ContentType.MULTIMODAL: [
        r"图表|图文|结合.*(图|表)|同时.*(图|表)",
    ],
}

# 显式意图关键词（命中即强命中，提升权重）
_EXPLICIT: Dict[ContentType, List[str]] = {
    ContentType.TABLE: [r"表格|table\b"],
    ContentType.IMAGE: [r"架构图|示意图|图片|image\b|diagram\b|figure\b"],
    ContentType.EQUATION: [r"公式|equation\b|formula\b"],
    ContentType.CROSS_DOCUMENT: [r"这几?篇|跨文档|compare (the|these) papers"],
    ContentType.MULTIMODAL: [r"图表|图文结合"],
}

_WORD_RE = re.compile(r"[A-Za-z0-9\-\u4e00-\u9fff]+")


class QueryAnalyzer:
    """Query Analyzer：判断用户问题意图。"""

    def __init__(
        self,
        classify_func: Optional[Callable[[str], str]] = None,
        llm_func: Optional[Callable[[str], str]] = None,
        use_llm_fallback: bool = True,
    ):
        self.classify_func = classify_func or (llm_func if use_llm_fallback else None)

    def classify(self, query: str) -> QueryIntent:
        """返回查询意图（确定性规则优先，其次可选 LLM 兜底）。"""
        intent, evidence = self._rule_based(query)
        if intent == ContentType.TEXT and self.classify_func is not None:
            try:
                label = (self.classify_func(query) or "").strip().lower()
                mapped = self._map_label(label)
                if mapped != ContentType.TEXT:
                    intent = mapped
            except Exception:
                pass
        return intent

    def classify_with_evidence(self, query: str) -> Dict[str, Any]:
        """返回 {intent, evidence, keywords}，便于调试与元数据输出。"""
        intent, evidence = self._rule_based(query)
        return {
            "intent": intent.value,
            "evidence": evidence,
            "keywords": self._extract_keywords(query),
        }

    # ------------------------------------------------------------------ #
    def _rule_based(self, query: str) -> (QueryIntent, List[str]):
        if not query or not query.strip():
            return ContentType.TEXT, []

        q = query.strip()
        low = q.lower()
        evidence: List[str] = []

        def hit(cfg: Dict[ContentType, List[str]], extra_weight: bool) -> bool:
            for ctype, patterns in cfg.items():
                for pat in patterns:
                    if re.search(pat, low) or re.search(pat, q):
                        if ctype not in evidence:
                            evidence.append(ctype.value)
                        if extra_weight:
                            evidence.append(f"{ctype.value}!explicit")
            return bool(evidence)

        # 多模态 = 同时命中 image 与 table（或显式「图表」）
        has_image = self._matches(ContentType.IMAGE, q, low)
        has_table = self._matches(ContentType.TABLE, q, low)
        explicit_mm = self._matches(ContentType.MULTIMODAL, q, low)

        if explicit_mm or (has_image and has_table):
            return ContentType.MULTIMODAL, self._mark(evidence, has_image, has_table, explicit_mm)

        # 显式跨文档 / 跨文档其它命中
        if self._matches(ContentType.CROSS_DOCUMENT, q, low) and self._has_multi_doc_language(low):
            return ContentType.CROSS_DOCUMENT, self._mark(evidence, True, False, False)

        # 单一模态强命中
        for ctype in (ContentType.TABLE, ContentType.IMAGE, ContentType.EQUATION):
            if self._matches_explicit(ctype, q, low):
                return ctype, self._mark(evidence, ctype == ContentType.IMAGE,
                                         ctype == ContentType.TABLE, False)
            if self._matches(ctype, q, low):
                return ctype, self._mark(evidence, ctype == ContentType.IMAGE,
                                         ctype == ContentType.TABLE, False)

        return ContentType.TEXT, evidence

    # ------------------------------------------------------------------ #
    @staticmethod
    def _matches(ctype: ContentType, q: str, low: str) -> bool:
        return any(re.search(p, low) or re.search(p, q) for p in _LEXICON[ctype])

    @staticmethod
    def _matches_explicit(ctype: ContentType, q: str, low: str) -> bool:
        return any(re.search(p, low) or re.search(p, q) for p in _EXPLICIT[ctype])

    @staticmethod
    def _mark(evidence, has_image, has_table, explicit_mm) -> List[str]:
        # 已有 evidence 足以说明，这里仅保证非空
        return evidence or ["text"]

    @staticmethod
    def _has_multi_doc_language(low: str) -> bool:
        # 跨文档通常带数量词或比较词；兼容阿拉伯数字与中文数字
        cn_num = r"(一|二|两|三|四|五|六|七|八|九|十|多|几)" + r"(篇|个|份)"
        return bool(
            re.search(r"\d+\s*(篇|文档|paper|documents|papers)", low)
            or re.search(cn_num + r"(文档|论文|报告|paper)", low)
            or re.search(r"(这几篇|多篇|跨文档|综述|总结.*(篇|文档))", low)
            or re.search(r"(之间|对比|不同)", low)
            or " across " in low
        )

    @staticmethod
    def _map_label(label: str) -> QueryIntent:
        mapping = {
            "text": ContentType.TEXT,
            "image": ContentType.IMAGE,
            "table": ContentType.TABLE,
            "equation": ContentType.EQUATION,
            "multimodal": ContentType.MULTIMODAL,
            "cross_document": ContentType.CROSS_DOCUMENT,
            "cross-document": ContentType.CROSS_DOCUMENT,
        }
        return mapping.get(label, ContentType.TEXT)

    @staticmethod
    def _extract_keywords(query: str) -> List[str]:
        return _WORD_RE.findall(query)[:12]


def analyze_query(query: str, **kwargs) -> QueryIntent:
    """便捷函数：默认规则分析。"""
    return QueryAnalyzer(**kwargs).classify(query)