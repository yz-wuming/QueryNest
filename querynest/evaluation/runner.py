"""
RAG Evaluation Runner

驱动数据集评测：对每条样例做检索（调用注入的 ``retriever``），计算 Recall@K /
Precision@K / MRR / NDCG@K，并按可用性附加 Faithfulness / Answer Relevancy
（未提供判定器/嵌入函数时标注 skipped，不伪造）。

最终产出 ``evaluation/results.json``。
"""

import json
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from querynest.core.exceptions import EvaluationError
from querynest.evaluation.dataset import EvalExample, load_dataset, write_results
from querynest.evaluation.metrics import (
    FaithfulnessEvaluator,
    AnswerRelevancyEvaluator,
    recall_at_k,
    precision_at_k,
    mrr,
    ndcg_at_k,
)


class EvalRunner:
    """评测运行器。retriever 回调入参为一个问题，返回 List[Dict]。"""

    def __init__(
        self,
        retriever: Callable[[str], List[Dict[str, Any]]],
        ks: Optional[List[int]] = None,
        judge_func: Optional[Callable[[str, str], float]] = None,
        embedding_func: Optional[Callable[[str], List[float]]] = None,
        output_path: str = "evaluation/results.json",
    ):
        self.retriever = retriever
        self.ks = ks or [5, 10]
        self.faithfulness = FaithfulnessEvaluator(judge_func=judge_func, embedding_func=embedding_func)
        self.relevancy = AnswerRelevancyEvaluator(embedding_func=embedding_func)
        self.output_path = output_path

    def run(self, dataset_path: str, top_k: int = 10) -> Dict[str, Any]:
        examples = load_dataset(dataset_path)
        per_example: List[Dict[str, Any]] = []
        aggregated: Dict[str, List[float]] = {f"recall@{k}": [] for k in self.ks}
        aggregated.update({f"precision@{k}": [] for k in self.ks})
        aggregated.update({"mrr@10": [], "ndcg@10": []})
        aggregated.update({"faithfulness": [], "answer_relevancy": []})

        started = time.perf_counter()
        for ex in examples:
            try:
                hits = list(self.retriever(ex.question) or [])[:top_k]
            except Exception as e:  # noqa: BLE001
                raise EvaluationError(f"检索失败: {e}", context={"question": ex.question})
            hits_expected = {"hits": hits, "expected": ex.expected_sources}
            row: Dict[str, Any] = {"question": ex.question}
            for k in self.ks:
                row[f"recall@{k}"] = round(recall_at_k(hits, ex.expected_sources, k), 4)
                row[f"precision@{k}"] = round(precision_at_k(hits, ex.expected_sources, k), 4)
                aggregated[f"recall@{k}"].append(row[f"recall@{k}"])
                aggregated[f"precision@{k}"].append(row[f"precision@{k}"])
            row["mrr@10"] = round(mrr(hits, ex.expected_sources, 10), 4)
            row["ndcg@10"] = round(ndcg_at_k(hits, ex.expected_sources, 10), 4)
            aggregated["mrr@10"].append(row["mrr@10"])
            aggregated["ndcg@10"].append(row["ndcg@10"])

            # 可选：答案相关指标
            fa = self.faithfulness.evaluate(_context_text(hits), ex.expected_answer or ex.question)
            row["faithfulness"] = fa.get("faithfulness")
            row["faithfulness_method"] = fa.get("method")
            if fa.get("faithfulness") is not None:
                aggregated["faithfulness"].append(fa["faithfulness"])

            re = self.relevancy.evaluate(ex.question, ex.expected_answer or "")
            row["answer_relevancy"] = re.get("answer_relevancy")
            if re.get("answer_relevancy") is not None:
                aggregated["answer_relevancy"].append(re["answer_relevancy"])

            per_example.append(row)

        elapsed = time.perf_counter() - started
        summary: Dict[str, Any] = {}
        for metric, values in aggregated.items():
            if metric.endswith("faithfulness") or metric.endswith("relevancy"):
                if values:
                    summary[metric] = round(statistics.mean(values), 4)
                else:
                    summary[metric] = None
            else:
                summary[metric] = round(statistics.mean(values), 4) if values else None

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dataset": dataset_path,
            "num_examples": len(examples),
            "top_k": top_k,
            "metrics": summary,
            "results": per_example,
            "elapsed_seconds": round(elapsed, 6),
        }

        write_results(report, self.output_path)
        return report

    def summarize(self, report: Dict[str, Any]) -> str:
        """生成人类可读摘要。"""
        m: Dict[str, Any] = report.get("metrics", {})
        order = []
        for k in self.ks:
            order += [f"recall@{k}", f"precision@{k}"]
        order += ["mrr@10", "ndcg@10", "faithfulness", "answer_relevancy"]

        lines = [
            f"Evaluation: {report.get('num_examples')} examples, "
            f"top_k={report.get('top_k')} (dataset: {report.get('dataset')})",
            "  metric           value",
        ]
        for metric in order:
            if metric in m:
                val = m[metric]
                lines.append(f"  {metric:<16} {val if val is not None else 'n/a (skipped)'}")
        return "\n".join(lines)


def _context_text(hits: List[Dict[str, Any]]) -> str:
    return "\n".join(str(h.get("text") or h.get("content") or "") for h in hits)