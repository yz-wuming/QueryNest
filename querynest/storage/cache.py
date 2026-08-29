"""
QueryNest 缓存

提供轻量、可落盘的 KV 缓存（可选使用场景：查询结果、解析指纹、文档处理状态）。
设计为自包含、无第三方依赖，方便测试；引擎同时保留对底层 LightRAG 缓存机制的
封装（见 core/engine）。
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


class Cache:
    """简单的文件型 JSON 缓存（按需落盘），支持 TTL。"""

    def __init__(
        self,
        namespace: str,
        storage_dir: str = "./querynest_storage/cache",
        ttl_seconds: Optional[float] = None,
        autosave: bool = True,
    ):
        self.namespace = namespace
        self.storage_dir = Path(storage_dir)
        self.ttl_seconds = ttl_seconds
        self._path = self.storage_dir / f"{namespace}.json"
        self._mem: dict = {}
        self._lock = threading.RLock()
        self._autosave = autosave
        self._load()

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            entry = self._mem.get(key)
        if entry is None:
            return default
        value, ts = entry
        if self.ttl_seconds is not None and (time.time() - ts) > self.ttl_seconds:
            with self._lock:
                self._mem.pop(key, None)
            if self._autosave:
                self.flush()
            return default
        return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._mem[key] = (value, time.time())
        if self._autosave:
            self.flush()

    def has(self, key: str) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def delete(self, key: str) -> bool:
        with self._lock:
            existed = key in self._mem
            if existed:
                del self._mem[key]
        if existed and self._autosave:
            self.flush()
        return existed

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
        self.flush()

    def keys(self):
        with self._lock:
            return list(self._mem.keys())

    def flush(self) -> None:
        with self._lock:
            data = {k: v for k, v in self._mem.items()}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._mem = {k: (v, 0.0) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            self._mem = {}


_MISSING = object()