"""QueryNest Evaluation & Retrieval Ablation —— 可复现的评测入口

用法示例::

    python scripts/run_evaluation.py --dataset evaluation/datasets/real_check.json --top-k 5
    python scripts/run_evaluation.py --dataset evaluation/datasets/example.json --out evaluation/benchmark.json

说明（诚实原则）:
- 从真实 QueryNest 引擎抓取检索策略（vector / keyword / hybrid / hybrid_rerank）,
  对每条样例执行真实检索并计时, 再用同一套指标计算 Recall/Precision/MRR/NDCG。
- 引擎无法初始化的策略（如 reranker 未激活、数据未索引）在结果中标记为
  ``not_available``+原因, 绝不伪造分数或 latency。
- 本脚本不要求任何 API Key; 若底层引擎缺少模型回调会中途如实报错并退出非零。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from querynest.evaluation.ablation import ablation_to_markdown, run_ablation
from querynest.evaluation.dataset import load_dataset


def _build_engine():
    from querynest import QueryNest, QueryNestConfig

    config = QueryNestConfig()
    engine = QueryNest(config)
    engine.apply_active_models()
    return engine


def main() -> int:
    parser = argparse.ArgumentParser(description="QueryNest 评测 / 检索消融")
    parser.add_argument("--dataset", default="evaluation/datasets/real_check.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--out", default="evaluation/benchmark.json")
    parser.add_argument("--datasets-dir", default="evaluation/datasets")
    parser.add_argument("--list-datasets", action="store_true", help="列出可用数据集后退出")
    args = parser.parse_args()

    datasets_dir = Path(args.datasets_dir)
    if args.list_datasets:
        for p in sorted(datasets_dir.glob("*.json")):
            print(p)
        return 0

    # 1) 加载数据集
    examples = load_dataset(args.dataset)
    print(f"loaded dataset: {args.dataset} ({len(examples)} examples), top_k={args.top_k}")

    # 2) 初始化引擎并抓取真实检索策略（失败则如实报告，不伪造）
    try:
        engine = _build_engine()
        import asyncio

        asyncio.run(engine._ensure_initialized())
        strategies = engine.retrieval_strategies()
    except Exception as e:  # noqa: BLE001
        print(f"[BLOCKED] 无法初始化引擎/检索器，无法执行真实消融: {e.__class__.__name__}: {e}",
              file=sys.stderr)
        print("说明: 需先配置可用 Embedding/LLM 模型回调并完成至少一次文档索引。", file=sys.stderr)
        return 2

    available = [n for n, fn in strategies.items() if callable(fn)]
    not_available = [n for n, fn in strategies.items() if not callable(fn)]
    if not_available:
        print(f"note: 以下策略当前 not_available: {', '.join(not_available)}")
    print(f"executing strategies: {available}")

    # 3) 真实执行消融
    result = run_ablation(strategies, examples, ks=[args.top_k], top_k=args.top_k)

    # 4) 落盘 + 打印
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"written: {out}")
    print("\n" + ablation_to_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())