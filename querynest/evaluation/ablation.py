"""Retrieval Ablation（检索消融）

在同一数据集、同一查询、同一 ground truth、同一 K 上，真实执行多种检索策略
（Vector / Keyword / Hybrid / Hybrid+Rerank），用同一套评估函数计算
Recall@K / Precision@K / MRR / NDCG@K，并记录每次检索的真实耗时。

设计原则：
- 每个策略的检索回调会被逐条样例真实调用，不在调用处伪造分数或耗时 ——
  耗时用 ``time.perf_counter`` 在真实调用处计时。
- 未提供的策略（如 reranker 未激活）如实标记 ``status=not_available`` 与原因，
  绝不用 0 或占位值冒充结果。
- 仅评估已实现的 ``run_ablation``，注入什么检索器就评估什么；不引入引擎依赖。
"""
from __future__ import annotations

import statistics
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from querynest.evaluation.dataset import EvalExample
from querynest.evaluation.metrics import (
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

# 检索策略回调约定：query -> List[Dict]（命中格式与 Hybrid Retrieval 一致）
RetrieverFn = Callable[[str], List[Dict[str, Any]]]

STRATEGIES = ("vector", "keyword", "hybrid", "hybrid_rerank")


def _score_row(retriever: RetrieverFn, example: EvalExample,
               ks: List[int], top_k: int) -> Dict[str, Any]:
    """真实执行单个策略在单条样例上的检索，计算指标并计时。"""
    started = time.perf_counter()
    hits = list(retriever(example.question) or [])[:top_k]
    latency_ms = (time.perf_counter() - started) * 1000.0

    row: Dict[str, Any] = {
        "question": example.question,
        "num_retrieved": len(hits),
        "latency_ms": round(latency_ms, 3),
    }
    for k in ks:
        row[f"recall@{k}"] = round(recall_at_k(hits, example.expected_sources, k), 4)
        row[f"precision@{k}"] = round(precision_at_k(hits, example.expected_sources, k), 4)
    row["mrr"] = round(mrr(hits, example.expected_sources, max(ks)), 4)
    row["ndcg"] = round(ndcg_at_k(hits, example.expected_sources, max(ks)), 4)
    return row


def _agg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return round(statistics.mean(values), 4)


def _run_strategy(name: str, retriever: RetrieverFn,
                  examples: List[EvalExample], ks: List[int],
                  top_k: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    per_metric: Dict[str, List[float]] = {}
    latencies: List[float] = []
    for ex in examples:
        try:
            row = _score_row(retriever, ex, ks, top_k)
        except Exception as e:  # noqa: BLE001
            return {
                "name": name,
                "status": "failed",
                "error": f"{e.__class__.__name__}: {e}",
                "metrics": {},
                "avg_latency_ms": None,
                "cases": rows,
            }
        rows.append(row)
        latencies.append(row["latency_ms"])
        for metric, val in row.items():
            if isinstance(val, (int, float)):
                per_metric.setdefault(metric, []).append(float(val))

    metrics = {metric: _agg(vals) for metric, vals in per_metric.items()
               if metric not in ("num_retrieved", "latency_ms")}
    return {
        "name": name,
        "status": "completed",
        "metrics": metrics,
        "avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else None,
        "cases": rows,
    }


def run_ablation(
    strategies: Dict[str, RetrieverFn],
    examples: List[EvalExample],
    ks: Optional[List[int]] = None,
    top_k: int = 10,
) -> Dict[str, Any]:
    """对多策略执行消融，返回可复现对比结果。

    :param strategies: ``{策略名(snake_case): 检索回调}``。无回调或值为 None 的
        策略不会执行，由调用方决定是否标记 not_available。
    :param examples: 已加载的评测样例（含 ground truth / expected_sources）。
    :param ks: 计算 Recall/Precision 的 top-K 集合；MRR / NDCG 使用 max(ks)。
    :param top_k: 每次检索最多返回的命中数。
    """
    ks = sorted(ks or [5])
    need = set(STRATEGIES)
    provided = set(n for n, fn in strategies.items() if callable(fn))
    unknown = provided - need
    if unknown:
        raise ValueError(f"未知策略名: {','.join(sorted(unknown))}（可用: {','.join(STRATEGIES)}）")

    result: Dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "num_examples": len(examples),
        "top_k": top_k,
        "ks": ks,
        "strategies": {},
    }
    for name in STRATEGIES:
        fn = strategies.get(name)
        if not callable(fn):
            result["strategies"][name] = {
                "name": name,
                "status": "not_available",
                "error": "该检索策略未提供检索器（可能依赖未激活的 reranker 或未索引数据）",
                "metrics": {},
                "avg_latency_ms": None,
                "cases": [],
            }
            continue
        result["strategies"][name] = _run_strategy(name, fn, examples, ks, top_k)
    return result


def ablation_to_markdown(result: Dict[str, Any]) -> str:
    """把消融结果渲染为人类可读的 Markdown 表格（如实区分 not_available）。"""
    ks = result.get("ks", [5])
    max_k = max(ks)
    headers = ["Strategy", "Status"] + [f"Recall@{k}" for k in ks] \
        + [f"Precision@{k}" for k in ks] + ["MRR", "NDCG", "Latency(ms)"]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join("---" for _ in headers) + "|"]
    for name, row in result.get("strategies", {}).items():
        status = row.get("status", "unknown")
        if status != "completed" or not row.get("metrics"):
            cells = [name, status] + ["n/a"] * (len(headers) - 2)
            lines.append("| " + " | ".join(str(c) for c in cells) + " |")
            continue
        metrics = row["metrics"]
        cells = [name, "completed"]
        for k in ks:
            cells.append(metrics.get(f"recall@{k}", "n/a"))
        for k in ks:
            cells.append(metrics.get(f"precision@{k}", "n/a"))
        cells.append(metrics.get("mrr", "n/a"))
        cells.append(metrics.get("ndcg", "n/a"))
        lat = row.get("avg_latency_ms")
        cells.append(lat if lat is not None else "n/a")
        lines.append("| " + " | ".join(str(c) for c in cells) + " |")
    return "\n".join(lines)