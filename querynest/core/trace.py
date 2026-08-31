"""QueryNest Trace / Observability（可观测性）

记录一次真实查询中各步骤的真实执行耗时、状态与关键元数据，用于：
- 解释“这次查询为什么这样/为什么失败”；
- 前端 Trace 面板渲染分步时间线；
- 性能观测（retrieval / rerank / generation 延迟）。

设计原则：
- 计时必须在真实代码执行处使用 ``time.perf_counter`` 进行（start→end），
  禁止在 API 层伪造固定 timing。
- 绝不记录 API Key / Secret / Authorization / 密码 / token / Cookie。
- 存储为进程内、有界、LRU 的环形容器，按 ``trace_id`` 可查。
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class TraceStep:
    """单个执行步骤。"""

    __slots__ = ("name", "status", "latency_ms", "metadata", "error")

    def __init__(
        self,
        name: str,
        status: str = "success",
        latency_ms: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        self.name = name
        self.status = status  # success | failed | skipped
        self.latency_ms = latency_ms
        self.metadata = metadata or {}
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 3),
        }
        if self.metadata:
            d["metadata"] = self.metadata
        if self.error:
            d["error"] = self.error
        return d


class QueryTrace:
    """一次查询的时间线记录。"""

    def __init__(
        self,
        query: str = "",
        mode: str = "mix",
        model_id: Optional[str] = None,
        provider: Optional[str] = None,
        max_steps: int = 64,
    ) -> None:
        self.trace_id = uuid.uuid4().hex[:16]
        self.query = query
        self.mode = mode
        self.model_id = model_id
        self.provider = provider
        self.started_at = _now()
        self.total_latency_ms = 0.0
        self.status = "running"
        self.error: Optional[str] = None
        self.citations: List[Any] = []
        self._steps: List[TraceStep] = []
        self._max_steps = max_steps
        self._started = time.perf_counter()

    def add_step(self, step: TraceStep) -> TraceStep:
        if len(self._steps) < self._max_steps:
            self._steps.append(step)
        return step

    def mark(
        self,
        name: str,
        status: str = "success",
        start: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> TraceStep:
        """记录一步。start 为 ``time.perf_counter()`` 的起点；省略则 latency=0。"""
        latency = 0.0
        if start is not None:
            latency = (time.perf_counter() - start) * 1000.0
        return self.add_step(TraceStep(name, status, latency, metadata, error))

    def finalize(self, citations: Optional[List[Any]] = None,
                 error: Optional[str] = None) -> None:
        self.total_latency_ms = (time.perf_counter() - self._started) * 1000.0
        if citations is not None:
            self.citations = citations
        self.status = "failed" if (error or self.error) else "completed"
        self.error = error or self.error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query,
            "mode": self.mode,
            "model_id": self.model_id,
            "provider": self.provider,
            "started_at": self.started_at,
            "status": self.status,
            "total_latency_ms": round(self.total_latency_ms, 3),
            "steps": [s.to_dict() for s in self._steps],
            "num_citations": len(self.citations),
            "error": self.error,
        }


class TraceStore:
    """进程内有界 Trace 容器（LRU）。拼接内容不含任何 Secret。"""

    def __init__(self, capacity: int = 256) -> None:
        self._capacity = capacity
        self._traces: "OrderedDict[str, QueryTrace]" = OrderedDict()

    def new(self, **kwargs) -> QueryTrace:
        trace = QueryTrace(**kwargs)
        return trace

    def put(self, trace: QueryTrace) -> None:
        self._traces[trace.trace_id] = trace
        self._traces.move_to_end(trace.trace_id)
        while len(self._traces) > self._capacity:
            self._traces.popitem(last=False)

    def get(self, trace_id: str) -> Optional[QueryTrace]:
        trace = self._traces.get(trace_id)
        if trace:
            self._traces.move_to_end(trace_id)
        return trace

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self._traces.values())
        return [t.to_dict() for t in items[-limit:]]

    def clear(self) -> None:
        self._traces.clear()


# 全局共享实例（进程内观测）
trace_store = TraceStore()