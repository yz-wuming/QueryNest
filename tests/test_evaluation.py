"""Evaluation metrics / dataset / runner tests (pure Python)."""

import json
import tempfile
from pathlib import Path

from querynest.evaluation.dataset import EvalExample, load_dataset
from querynest.evaluation.metrics import (
    recall_at_k,
    precision_at_k,
    mrr,
    ndcg_at_k,
    lexical_faithfulness,
    FaithfulnessEvaluator,
    AnswerRelevancyEvaluator,
)
from querynest.evaluation.runner import EvalRunner


def _hit(text="x", cid="c1", doc="paper.pdf", page=2):
    return {"document_name": doc, "content": text, "chunk_id": cid,
            "document_id": "d1", "page": page, "type": "text"}


def _empty_hits(q):
    return []


# ---------------- Metrics ----------------
def test_recall_at_k_full():
    hits = [_hit(doc="paper.pdf"), _hit(doc="paper.pdf", cid="c2")]
    src = ["paper.pdf#2"]
    assert recall_at_k(hits, src, 5) == 1.0


def test_recall_at_k_partial():
    src = ["paper.pdf#4", "unknown.pdf"]
    assert recall_at_k([_hit(doc="paper.pdf")], src, 5) == 0.5


def test_recall_at_k_empty():
    assert recall_at_k([], [], 5) == 0.0


def test_precision_at_k():
    hits = [_hit(doc="paper.pdf"), _hit(doc="other.pdf")]
    # 检索了2条命中，1条相关 → 精确率 = 1/2 = 0.5（precision@k 分母为 k）
    assert precision_at_k(hits, ["paper.pdf#2"], 2) == 0.5


def test_precision_at_k_empty():
    assert precision_at_k([], ["paper.pdf"], 5) == 0.0


def test_recall_at_k_filename_with_digit():
    # 回归：文件名含数字（如 ph7_readme_kb.txt）不得被页码正则截断
    hits = [_hit(doc="ph7_readme_kb.txt")]
    assert recall_at_k(hits, ["ph7_readme_kb.txt"], 5) == 1.0
    assert mrr(hits, ["ph7_readme_kb.txt"], 5) == 1.0
    assert precision_at_k(hits, ["ph7_readme_kb.txt"], 5) > 0.0


def test_norm_page_suffix_forms():
    from querynest.evaluation.metrics import _split_doc_page

    assert _split_doc_page("paper.pdf#4") == ("paper.pdf", 4)
    assert _split_doc_page("paper.pdf# 4") == ("paper.pdf", 4)
    assert _split_doc_page("paper.pdf — Page 4") == ("paper.pdf", 4)
    assert _split_doc_page("paper.pdf - Page 4") == ("paper.pdf", 4)
    assert _split_doc_page("ph7_readme_kb.txt") == ("ph7_readme_kb.txt", 0)


def test_mrr_first():
    hits = [_hit(doc="paper.pdf"), _hit(doc="other.pdf")]
    assert mrr(hits, ["paper.pdf#2"], 5) == 1.0


def test_mrr_second():
    hits = [_hit(doc="other.pdf"), _hit(doc="paper.pdf")]
    assert mrr(hits, ["paper.pdf#2"], 5) == 0.5


def test_mrr_not_found():
    assert mrr([_hit(doc="other.pdf")], ["paper.pdf#2"], 5) == 0.0


def test_ndcg_at_k():
    hits = [_hit(doc="paper.pdf"), _hit(doc="other.pdf")]
    val = ndcg_at_k(hits, ["paper.pdf#2"], 5)
    assert 0.0 < val <= 1.0


def test_lexical_faithfulness():
    ctx = "model accuracy is 95 percent"
    answer = "model accuracy is 95 percent"
    assert lexical_faithfulness(answer, ctx) > 0.5


def test_lexical_faithfulness_no_overlap():
    assert lexical_faithfulness("completely different topic", "about model accuracy") < 0.5


# ---------------- Dataset ----------------
def test_eval_example_requires_question():
    import pytest

    from querynest.core.exceptions import EvaluationError

    with pytest.raises(EvaluationError):
        EvalExample.from_dict({"expected_answer": "x"})


def test_load_dataset_json(tmp_path):
    p = tmp_path / "test.json"
    p.write_text(json.dumps([
        {"question": "q1", "expected_sources": ["a.pdf"]},
        {"question": "q2", "expected_sources": ["b.pdf"]},
    ]), encoding="utf-8")
    exs = load_dataset(str(p))
    assert len(exs) == 2
    assert exs[0].question == "q1"


def test_runner_with_dummy_retriever(tmp_path):
    runner = EvalRunner(retriever=_empty_hits, output_path=str(tmp_path / "out.json"))
    dataset = str(tmp_path / "d.json")
    Path(dataset).write_text(json.dumps([
        {"question": "q1", "expected_sources": ["a.pdf"]},
    ]), encoding="utf-8")
    report = runner.run(dataset, top_k=5)
    assert report["num_examples"] == 1
    assert report["metrics"]["recall@5"] == 0.0


def test_faithfulness_evaluator_default():
    ev = FaithfulnessEvaluator()
    result = ev.evaluate("context text", "answer text")
    assert "faithfulness" in result
    assert result["method"] == "lexical_heuristic"


def test_answer_relevancy_skipped():
    ev = AnswerRelevancyEvaluator()
    result = ev.evaluate("question", "answer")
    assert result["answer_relevancy"] is None
    assert result["skipped"] is True