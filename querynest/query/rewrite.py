"""
Query Rewrite

在多轮上下文中把模糊/指代型的用户问题改写为自包含、可检索的完整问题。

默认采用确定性改写（合并前文主语与问题），可注入 ``llm_func`` 做更自然的语义改写。
"""

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple


# 指代性 / 从属性标记：出现这些词说明问题依赖上文
_DEICTIC = re.compile(
    r"它|它们|这个|那个|这种|这些|上述|该\w*|为何|为什么|如何|why|it\b|this\b|these\b"
    r"|its\b|which|the above|mentioned"
)

# 抽取上一轮问题中的"主语/主题"（去掉疑问词后的核心片段）
_QUESTION_STRIP = re.compile(
    r"^(请问|问一下|请|is|are|what|what is|what are|how|how does|how do|why|which|"
    r"when|where|who|given|regarding|关于|请解释|解释一下)[：:，,\s]*",
    re.IGNORECASE,
)


@dataclass
class Turn:
    """一轮对话记录。"""

    user: str
    assistant: str = ""


@dataclass
class RewriteContext:
    """供改写用的上下文：多轮记录 + 可选文档上下文。"""

    history: List[Turn] = field(default_factory=list)
    documents: List[str] = field(default_factory=list)  # 当前文档名，用于定位


class QueryRewriter:
    """Query Rewitter：把带指代/从属关系的简短问题展开为完整问题。"""

    def __init__(self, llm_func: Optional[Callable[[str], str]] = None):
        self.llm_func = llm_func

    def rewrite(
        self,
        current_question: str,
        history: Optional[List[Turn]] = None,
        documents: Optional[List[str]] = None,
    ) -> str:
        """改写问题；若无历史且问题本身完整，则原样返回。"""
        history = history or []
        documents = documents or []

        # 无指代需求 -> 原样
        if not _DEICTIC.search(current_question) or not history:
            return current_question.strip()

        # 1) 尝试 LLM 改写
        if self.llm_func is not None:
            try:
                rewritten = self.llm_func(current_question)
                if rewritten and len(rewritten.strip()) > len(current_question.strip()):
                    return rewritten.strip()
            except Exception:
                pass  # 回退到规则改写

        # 2) 规则改写：合并最近有意义的一轮上文
        prev_user = history[-1].user
        subject = self._extract_subject(prev_user)
        doc_ctx = ""
        if documents:
            doc_ctx = "（文档：" + "、".join(documents[:3]) + "）"

        if subject:
            return (
                f"结合此前关于「{subject}」的讨论{doc_ctx}，完善后的完整问题是："
                f"{current_question.strip()}"
            )
        return (
            f"基于上一轮问题「{prev_user.strip()}」{doc_ctx}的完整问题：{current_question.strip()}"
        )

    @staticmethod
    def _extract_subject(prev_user: str) -> str:
        """从上一轮问题抽取核心主语（取首个含名词成分的片段）。"""
        q = _QUESTION_STRIP.sub("", prev_user).strip()
        # 截取到第一个问号/分句结束
        cut = re.split(r"[？?。;,，]", q)[0].strip()
        # 去掉尾部 "什么样的/如何/为什么" 等
        cut = re.sub(r"(是什么样的|是如何|为什么|怎么样|多少|几个)$", "", cut).strip()
        if len(cut) > 80:
            cut = cut[:80] + "…"
        return cut


def rewrite_query(
    current_question: str,
    history: Optional[List[Turn]] = None,
    documents: Optional[List[str]] = None,
    llm_func: Optional[Callable[[str], str]] = None,
) -> str:
    """便捷函数：改写查询。"""
    return QueryRewriter(llm_func=llm_func).rewrite(
        current_question, history=history, documents=documents
    )