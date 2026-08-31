"""
QueryNest 模型注册表（Model Registry）—— 运行时可管理的模型配置。

- 持久化到 ``<working_dir>/models.json``（复用现有存储目录，不引入新数据库）。
- 首次运行会从 ``QueryNestConfig``（``QUERYNEST_*`` / ``.env``）播种默认模型，
  因此不配置界面时行为与现状完全一致。
- API Key 明文仅写本地注册文件，任何读到前端的响应都只返回掩码 ``***xxxx``。

使用方式::

    reg = ModelRegistry(cfg.working_dir)
    model = reg.resolve("chat")            # 当前生效的聊天模型
    reg.activate("m-xxxx")                 # 把该模型设为所在用途的默认
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

from querynest.core.config import QueryNestConfig
from querynest.core.secrets import SecretStore

KINDS = ("chat", "vision", "embedding", "reranker")
KIND_LABEL = {
    "chat": "聊天",
    "vision": "视觉",
    "embedding": "向量嵌入",
    "reranker": "重排",
}
BINDING_LABEL = {
    "openai": "OpenAI",
    "bigmodel": "智谱",
    "deepseek": "DeepSeek",
    "qwen": "Qwen",
    "dashscope": "DashScope",
    "ollama": "Ollama",
}


def infer_provider(base_url: str = "", model: str = "") -> str:
    """根据 Base URL（无法判断时看模型名）推断提供商，避免把智谱/DeepSeek 等标成 OpenAI。"""
    b = (base_url or "").lower()
    m = (model or "").lower()
    if "bigmodel.cn" in b or b.startswith("https://open.bigmodel"):
        return "bigmodel"
    if "deepseek" in b or m.startswith("deepseek"):
        return "deepseek"
    if "dashscope" in b or "aliyuncs.com" in b or "dashscope" in m:
        return "qwen"
    if "11434" in b or "localhost" in b or "127.0.0.1" in b or "0.0.0.0" in b:
        return "ollama"
    if "openai.com" in b or "api.openai" in b:
        return "openai"
    return "openai"  # 兜底：OpenAI 兼容格式


class RegistryError(Exception):
    pass


@dataclass
class ModelEntry:
    id: str = ""
    kind: str = "chat"  # chat / vision / embedding / reranker
    name: str = ""  # 展示名，例如 GLM-4-Flash
    provider: str = ""  # openai / bigmodel / ollama / ...
    model: str = ""  # 模型标识
    base_url: str = ""
    api_key: str = ""
    dimension: int = 0  # embedding 专用
    enabled: bool = True
    is_default: bool = False
    created_at: str = ""

    def masked_api_key(self) -> str:
        k = self.api_key or ""
        return ("***" + k[-4:]) if k else ""

    def to_dict(self, secret: bool = False) -> Dict[str, Any]:
        d = asdict(self)
        d["has_api_key"] = bool(self.api_key)
        if not secret:
            # 对外只给掩码 + 布尔标记，绝不返回真实 Secret
            d["api_key"] = self.masked_api_key()
        return d


class ModelRegistry:
    """模型注册表：CRUD + 激活默认 + 按用途解析当前生效模型。"""

    def __init__(self, working_dir: str):
        self.path = Path(working_dir) / "models.json"
        self.secrets = SecretStore(working_dir)
        self._entries: List[ModelEntry] = []
        self.load()

    # ---------------- 初始化 / 持久化 ----------------
    def _seed_from_config(self) -> None:
        cfg = QueryNestConfig()
        now = str(int(time.time()))
        self._entries = [
            ModelEntry(
                id="chat-default", kind="chat", name=cfg.llm_model,
                provider=infer_provider(cfg.llm_base_url, cfg.llm_model),
                model=cfg.llm_model,
                base_url=cfg.llm_base_url, api_key=cfg.llm_api_key,
                enabled=True, is_default=True, created_at=now,
            ),
            ModelEntry(
                id="vision-default", kind="vision",
                name=cfg.vision_model or cfg.llm_model,
                provider=infer_provider(cfg.llm_base_url, cfg.vision_model or cfg.llm_model),
                model=cfg.vision_model or cfg.llm_model, base_url=cfg.llm_base_url,
                api_key=cfg.llm_api_key, enabled=True, is_default=True, created_at=now,
            ),
            ModelEntry(
                id="embedding-default", kind="embedding", name=cfg.embedding_model,
                provider=infer_provider(cfg.llm_base_url, cfg.embedding_model),
                model=cfg.embedding_model,
                base_url=cfg.llm_base_url,
                api_key=cfg.embedding_binding_api_key or cfg.llm_api_key,
                dimension=cfg.embedding_dim, enabled=True, is_default=True,
                created_at=now,
            ),
            ModelEntry(
                id="reranker-default", kind="reranker",
                name=cfg.reranker_model or "（未启用）",
                provider="", model=cfg.reranker_model or "",
                base_url="", api_key="", enabled=bool(cfg.enable_rerank),
                is_default=True, created_at=now,
            ),
        ]
        self._save()

    def load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                items: List[ModelEntry] = []
                for d in raw:
                    if not isinstance(d, dict):
                        continue
                    d = self._normalize_model_dict(d)  # 旧 schema 迁移 + 剥离 Secret
                    items.append(self._hydrate_secret(ModelEntry(**d)))
                self._entries = items
                self._save()  # 把旧 schema 持久化迁移成新格式（Secret 已剥离）
                return
            except Exception:  # noqa: BLE001 —— 损坏时回退到播种
                pass
        self._seed_from_config()

    @staticmethod
    def _normalize_model_dict(d: Dict[str, Any]) -> Dict[str, Any]:
        """兼容旧版 models.json schema（``type``/``default``/内含 Secret 等）。

        迁移后 metadata 与 Secret 严格分离：真实 Key 一律不保留在磁盘元数据里。
        """
        field_names = {f.name for f in fields(ModelEntry)}
        out: Dict[str, Any] = {}
        # kind：兼容历史字段 `type`
        kind = d.get("kind") or d.get("type")
        if kind:
            out["kind"] = str(kind).lower()
        # default / is_default 归一化
        if "is_default" in d:
            out["is_default"] = bool(d.get("is_default"))
        elif "default" in d:
            out["is_default"] = bool(d.get("default"))
        for k, v in d.items():
            if k in ("kind", "type", "default", "is_default"):  # 已归一化
                continue
            if k in ("api_key", "apiKey", "secret", "secret_key", "token"):  # 绝不进元数据
                continue
            if k in field_names:
                out[k] = v
        return out

    def _hydrate_secret(self, e: ModelEntry) -> ModelEntry:
        """为模型补充 Secret：优先 SecretStore，未命中时回退默认模型的环境变量。"""
        if not e.api_key:
            e.api_key = self.secrets.get(e.id) or self._env_seed_key(e.id)
        return e

    @staticmethod
    def _env_seed_key(mid: str) -> str:
        cfg = QueryNestConfig()
        if mid == "chat-default" or mid == "vision-default":
            return cfg.llm_api_key or ""
        if mid == "embedding-default":
            return cfg.embedding_binding_api_key or cfg.llm_api_key or ""
        return ""

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict(secret=True) for e in self._entries]
        for d in data:  # Secret 不进 models.json（独立存于 SecretStore）
            d.pop("api_key", None)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ---------------- 查询 ----------------
    def list(self, kind: Optional[str] = None) -> List[ModelEntry]:
        items = [e for e in self._entries if kind is None or e.kind == kind]
        order = {k: i for i, k in enumerate(KINDS)}
        return sorted(items, key=lambda e: (order.get(e.kind, 9), e.name.lower()))

    def get(self, mid: str) -> ModelEntry:
        for e in self._entries:
            if e.id == mid:
                return e
        raise RegistryError(f"模型不存在: {mid}")

    def resolve(self, kind: str, model_id: Optional[str] = None) -> ModelEntry:
        """返回当前生效模型：优先 ``model_id``，否则取该用途的默认（且启用）项。"""
        kind = (kind or "").lower()
        if kind not in KINDS:
            raise RegistryError(f"模型用途必须为 {', '.join(KINDS)}")
        if model_id:
            e = self.get(model_id)
            if e.kind != kind:
                raise RegistryError(f"模型 {model_id} 用途为 {e.kind}，非 {kind}")
            return e
        for e in self._entries:
            if e.kind == kind and e.is_default and e.enabled:
                return e
        for e in self._entries:
            if e.kind == kind and e.enabled:
                return e
        raise RegistryError(f"没有可用的 {kind} 模型")

    def active_embedding_dim(self) -> int:
        try:
            return self.resolve("embedding").dimension or 0
        except RegistryError:
            return 0

    # ---------------- 写入 ----------------
    def add(self, data: Dict[str, Any], confirm: bool = False) -> ModelEntry:
        kind = str(data.get("kind") or "").lower()
        if kind not in KINDS:
            raise RegistryError(f"模型用途必须为 {', '.join(KINDS)}")
        model = str(data.get("model") or "").strip()
        if not model:
            raise RegistryError("model（模型标识）必填")
        self._check_embedding_dim(kind, data.get("dimension"), confirm)
        e = ModelEntry(
            id="m-" + uuid.uuid4().hex[:8],
            kind=kind,
            name=str(data.get("name") or "").strip() or model,
            provider=str(data.get("provider") or "openai").strip(),
            model=model,
            base_url=str(data.get("base_url") or "").strip(),
            api_key=str(data.get("api_key") or "").strip(),
            dimension=int(data.get("dimension") or 0),
            enabled=bool(data.get("enabled", True)),
            is_default=False,
            created_at=str(int(time.time())),
        )
        self._entries.append(e)
        if e.api_key:
            self.secrets.set(e.id, e.api_key)
        self._save()
        return e

    def update(self, mid: str, patch: Dict[str, Any], confirm: bool = False) -> ModelEntry:
        e = self.get(mid)
        kind = e.kind
        allowed = {"name", "provider", "model", "base_url", "api_key",
                   "dimension", "enabled"}
        for k, v in patch.items():
            if k not in allowed:
                continue
            if k == "api_key" and not v:
                continue  # 留空表示保留原 Key
            if k == "enabled":
                v = bool(v)
            if k == "dimension":
                v = int(v or 0)
            setattr(e, k, v)
        if kind == "embedding":
            self._check_embedding_dim(kind, e.dimension, confirm)
        if not e.name:
            e.name = e.model
        if patch.get("api_key"):
            self.secrets.set(e.id, e.api_key)
        self._save()
        return e

    def delete(self, mid: str) -> None:
        e = self.get(mid)
        if e.is_default:
            raise RegistryError(
                "当前为默认模型，不能删除。请先把该用途的其他模型设为默认后再删除。"
            )
        self._entries = [x for x in self._entries if x.id != mid]
        self.secrets.delete(mid)
        self._save()

    def activate(self, mid: str) -> ModelEntry:
        e = self.get(mid)
        for x in self._entries:
            if x.kind == e.kind:
                x.is_default = x.id == mid
        self._save()
        return e

    def set_default(self, mid: str) -> ModelEntry:
        """同一种用途最多一个 default：设置新 default 时自动取消旧 default。"""
        return self.activate(mid)

    def enable(self, mid: str) -> ModelEntry:
        e = self.get(mid)
        e.enabled = True
        self._save()
        return e

    def disable(self, mid: str) -> ModelEntry:
        e = self.get(mid)
        e.enabled = False
        self._save()
        return e

    # ---------------- Embedding 维度护栏 ----------------
    def _check_embedding_dim(self, kind: str, dimension, confirm: bool) -> None:
        """Embedding 维度与现存索引不一致时，拒绝直接切换（除非确认重索引）。"""
        if kind != "embedding":
            return
        new_dim = int(dimension or 0)
        active_dim = self.active_embedding_dim()
        if new_dim and active_dim and new_dim != active_dim and not confirm:
            raise RegistryError(
                f"新 Embedding 维度 {new_dim} 与当前索引维度 {active_dim} 不一致，"
                "会导致现有向量索引失效、需要重新索引文档。请确认后重试（confirm=true）。"
            )