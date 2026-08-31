"""Query Analyzer / Query Rewrite tests (pure Python)."""

from querynest.core.models import ContentType
from querynest.query.analyzer import QueryAnalyzer, analyze_query
from querynest.query.rewrite import QueryRewriter, Turn


# ---------------- Query Analyzer ----------------
def test_analyzer_table():
    assert analyze_query("这个表格中哪个模型效果最好？") == ContentType.TABLE


def test_analyzer_image():
    assert analyze_query("这个架构图说明了什么？") == ContentType.IMAGE


def test_analyzer_equation():
    assert analyze_query("这个公式是如何推导的？") == ContentType.EQUATION


def test_analyzer_cross_document():
    assert analyze_query("比较这三篇论文的实验结果。") == ContentType.CROSS_DOCUMENT


def test_analyzer_multimodal_explicit():
    assert analyze_query("结合图表说明趋势。") == ContentType.MULTIMODAL


def test_analyzer_text_fallback():
    assert analyze_query("什么是检索增强生成？") == ContentType.TEXT


def test_analyzer_classify_with_evidence():
    info = QueryAnalyzer().classify_with_evidence("这个表格中哪个模型效果最好")
    assert info["intent"] == ContentType.TABLE.value
    assert isinstance(info["keywords"], list)


def test_analyzer_llm_fallback_used():
    def fake(q):
        return "IMAGE"

    analyzer = QueryAnalyzer(classify_func=fake)
    # 规则会判为 TEXT，但 LLM 兜底改判为 IMAGE
    assert analyzer.classify("colored drawing") == ContentType.IMAGE


# ---------------- Query Rewrite ----------------
def test_rewrite_no_history_identity():
    q = "这个模型为什么更好？"
    assert QueryRewriter().rewrite(q) == q


def test_rewrite_with_history_expands_query():
    history = [Turn(user="论文X中提出的模型相比Baseline Y表现更好", assistant="是的。")]
    rewritten = QueryRewriter().rewrite("为什么这个模型更好？", history=history)
    assert rewritten != "为什么这个模型更好？"
    assert len(rewritten) > len("为什么这个模型更好？")


def test_rewrite_llm_first():
    def fake(q):
        return "论文X中提出的模型在指标Z上为何比Baseline更好？"

    rewritten = QueryRewriter(llm_func=fake).rewrite(
        "为什么更好？", history=[Turn(user="讨论论文X")], documents=["paper.pdf"]
    )
    assert "论文X" in rewritten


def test_rewrite_context_bound():
    """自包含问题不应被改写。"""
    q = "什么是混合检索？"
    assert QueryRewriter().rewrite(q, history=[Turn(user="上一轮问题", assistant="回答")]) == q