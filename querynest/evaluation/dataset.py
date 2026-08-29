"""
Evaluation 数据集加载器

支持 JSON / JSONL 测试集，统一为内部结构。
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Union

from querynest.core.exceptions import EvaluationError


class EvalExample:
    """一条评测样例。"""

    __slots__ = ("question", "expected_answer", "expected_sources", "metadata")

    def __init__(self, question, expected_answer="", expected_sources=None, metadata=None):
        self.question = str(question or "").strip()
        self.expected_answer = str(expected_answer or "")
        self.expected_sources = list(expected_sources or [])
        self.metadata = metadata or {}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvalExample":
        required = "question" in d and str(d.get("question") or "").strip()
        if not required:
            raise EvaluationError("测试样例缺少非空字段: question", context={"item": d})
        return cls(
            question=d["question"],
            expected_answer=d.get("expected_answer", ""),
            expected_sources=d.get("expected_sources", []),
            metadata={k: v for k, v in d.items() if k not in ("question", "expected_answer", "expected_sources")},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question": self.question,
            "expected_answer": self.expected_answer,
            "expected_sources": self.expected_sources,
            **self.metadata,
        }


def load_dataset(path) -> List[EvalExample]:
    """从 JSON（列表 或 含 items/数据数组）或 JSONL 加载测试集。"""
    p = Path(path)
    if not p.exists():
        raise EvaluationError(f"数据集不存在: {path}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise EvaluationError(f"读取数据集失败: {e}", context={"path": str(p)}) from e

    loaded: List[dict]

    if p.suffix.lower() == ".jsonl":
        loaded = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                loaded.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise EvaluationError(f"JSONL 行解析失败: {e}", context={"line": line[:200]}) from e
    else:
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise EvaluationError(f"JSON 数据集解析失败: {e}", context={"path": str(p)}) from e
        if isinstance(raw, dict):
            items = raw.get("items") or raw.get("data") or raw.get("examples") or raw.get("questions")
            if items is None and raw.get("question"):
                items = [raw]
            if not isinstance(items, list):
                raise EvaluationError("数据集结构无法识别（需要 JSON 数组 或包含 items/data/examples 的对象）")
            loaded = items
        elif isinstance(raw, list):
            loaded = raw
        else:
            raise EvaluationError("数据集根节点必须是数组或对象")

    examples = []
    for i, item in enumerate(loaded):
        if not isinstance(item, dict):
            raise EvaluationError(f"第 {i + 1} 条数据不是对象", context={"item": item})
        try:
            examples.append(EvalExample.from_dict(item))
        except EvaluationError as e:
            raise EvaluationError(f"数据集第 {i + 1} 条不合法: {e.message}") from e
    if not examples:
        raise EvaluationError("数据集中没有有效样例")
    return examples


def write_results(results: List[Dict[str, Any]], output_path) -> None:
    """把评测结果写入 JSON 文件。"""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")