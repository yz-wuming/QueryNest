"""
QueryNest Provider Adapter —— 不同模型提供商的调用差异抽象层。

上层（Model Registry / Models API / Engine）只依赖本模块，无需关心各家协议细节：

- ``OpenAICompatibleAdapter``：OpenAI、DeepSeek、Qwen、智谱 GLM、DashScope 以及任意
  OpenAI-compatible 服务，全部通过 ``base_url`` + ``model`` + ``api_key`` 实现，
  不针对每家公司重复写代码。
- ``OllamaAdapter``：本地 / 自定义 Ollama 服务，不需要 API Key。
- ``AnthropicAdapter`` / ``GeminiAdapter``：架构预留（真实协议未接入，标记 NOT TESTED）。
- ``StubAdapter``：未知/自定义 Provider，明确返回 not_supported。

每个 Adapter 声明其支持的 kind（chat / vision / embedding / reranker），并提供
``test_connection`` 做**真实**连通性测试（不是只查 Key 是否存在），并把异常分类为
可读的错误（401 / 403 / 404 / 429 / 500 / timeout / network / invalid model）。

真实外部连通性测试（真实 Key）在无 Key 环境下标记为 NOT TESTED；Adapter 的逻辑本身
可用 mock 离线单测验证。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from querynest.core.model_registry import ModelEntry

# kind 名称（与 Model Registry / KINDS 保持一致）
KIND_CHAT = "chat"
KIND_VISION = "vision"
KIND_EMBEDDING = "embedding"
KIND_RERANKER = "reranker"


@dataclass
class TestResult:
    """连通性测试结果。绝不包含真实 Secret。"""

    ok: bool
    message: str = ""
    category: str = ""  # ok/auth/permission/not_found/not_configured/rate_limit/server_error/timeout/network/invalid_model/not_supported/invalid_kind/invalid_provider/unknown
    latency_ms: Optional[float] = None
    model: str = ""
    provider: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "category": self.category,
            "latency_ms": self.latency_ms,
            "model": self.model,
            "provider": self.provider,
            "error": self.error,
        }


class _HttpStatusError(Exception):
    """携带 HTTP 状态码的异常（Ollama urllib 路径用，供统一错误分类）。"""

    def __init__(self, code: int, body: str = ""):
        self.status_code = code
        self.body = body
        super().__init__(f"HTTP {code}: {body[:200]}")


def _http_status(exc: Exception) -> Optional[int]:
    return getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )


def classify_error(exc: Exception, model: str = "") -> tuple:
    """把任意底层异常分类为 ``(category, readable_message)``。

    正确区分 401 / 403 / 404 / 429 / 500 / timeout / network / invalid model，
    避免一律变成 "Connection failed"。
    """
    code = _http_status(exc)
    name = type(exc).__name__
    low = str(exc).lower()
    if code == 401:
        return "auth", "认证失败：API Key 无效或已过期"
    if code == 403:
        return "permission", "权限不足：该 API Key 无权访问此模型"
    if code == 404:
        return "not_found", "资源不存在或模型不可用（模型标识 / endpoint 可能无效）"
    if code == 429:
        return "rate_limit", "请求过于频繁（Rate limit），请稍后重试"
    if code and code >= 500:
        return "server_error", f"上游服务错误（HTTP {code}）"
    if "timeout" in low or "timed out" in low or "Timeout" in name:
        return "timeout", "连接超时，请检查网络或稍后重试"
    if (
        "connection" in low
        or "network" in low
        or "NameResolution" in low
        or "ConnectionError" in name
        or "APIConnectionError" in name
    ):
        return "network", "网络错误，无法连接到指定服务"
    if (
        ("model" in low or "code model_not_found" in low)
        and ("not found" in low or "does not exist" in low or "model_not_found" in low or "invalid" in low)
    ):
        return "invalid_model", f"模型不可用或不存在：{model}"
    return "unknown", f"连接失败：{type(exc).__name__}: {exc}"


# -------------------------------------------------------------------- #
# 基类
# -------------------------------------------------------------------- #
class ProviderAdapter:
    provider = "generic"
    supports_kinds = (KIND_CHAT, KIND_VISION, KIND_EMBEDDING)
    requires_api_key = True

    def test_connection(self, entry: ModelEntry) -> TestResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def _guard_kind(self, entry: ModelEntry) -> Optional[TestResult]:
        if entry.kind not in self.supports_kinds:
            if entry.kind == KIND_RERANKER:
                return TestResult(
                    ok=False, message="重排模型的真实连接测试需要独立 Rerank API，当前未接入（NOT TESTED）。",
                    category="not_supported", model=entry.model, provider=self.provider,
                    error="reranker_not_supported",
                )
            return TestResult(
                ok=False, message=f"Provider {self.provider} 不支持该用途 kind={entry.kind}。",
                category="invalid_kind", model=entry.model, provider=self.provider,
                error=f"kind_not_supported:{entry.kind}",
            )
        return None


# -------------------------------------------------------------------- #
# OpenAI-Compatible（OpenAI / DeepSeek / Qwen / 智谱 GLM / DashScope / 自定义）
# -------------------------------------------------------------------- #
class OpenAICompatibleAdapter(ProviderAdapter):
    provider = "openai-compatible"
    supports_kinds = (KIND_CHAT, KIND_VISION, KIND_EMBEDDING)

    def test_connection(self, entry: ModelEntry) -> TestResult:
        model = entry.model
        guard = self._guard_kind(entry)
        if guard:
            return guard
        if not entry.api_key:
            return TestResult(
                ok=False, message="API key is not configured", category="not_configured",
                model=model, provider=self.provider, error="missing_api_key",
            )
        import openai  # 延迟导入；未装 openai 也会在真实测试时给出清晰错误

        client = openai.OpenAI(
            api_key=entry.api_key, base_url=entry.base_url or None,
            timeout=10.0, max_retries=0,
        )
        t0 = time.perf_counter()
        try:
            if entry.kind == KIND_EMBEDDING:
                client.embeddings.create(model=model, input="ping")
            else:
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                )
            ms = int((time.perf_counter() - t0) * 1000)
            return TestResult(
                ok=True, message=f"Connection successful：{model} 可用",
                category="ok", latency_ms=ms, model=model, provider=self.provider,
            )
        except Exception as exc:  # noqa: BLE001 —— 分类成可读错误
            cat, msg = classify_error(exc, model)
            return TestResult(
                ok=False, message=msg, category=cat, model=model,
                provider=self.provider, error=f"{type(exc).__name__}: {exc}",
            )


# -------------------------------------------------------------------- #
# Ollama（本地 / 自定义，无需 API Key）
# -------------------------------------------------------------------- #
class OllamaAdapter(ProviderAdapter):
    provider = "ollama"
    supports_kinds = (KIND_CHAT, KIND_VISION, KIND_EMBEDDING)
    requires_api_key = False
    DEFAULT_BASE_URL = "http://localhost:11434"

    def _base(self, entry: ModelEntry) -> str:
        b = (entry.base_url or "").strip().rstrip("/")
        return b or self.DEFAULT_BASE_URL

    def test_connection(self, entry: ModelEntry) -> TestResult:
        model = entry.model
        guard = self._guard_kind(entry)
        if guard:
            return guard
        base = self._base(entry)
        if not model:
            return TestResult(
                ok=False, message="Model（模型标识）必填", category="invalid_model",
                model=model, provider=self.provider, error="missing_model",
            )
        t0 = time.perf_counter()
        try:
            if entry.kind == KIND_EMBEDDING:
                _http_json(base + "/api/embeddings", {"model": model, "prompt": "ping"})
            else:
                _http_json(
                    base + "/api/generate",
                    {"model": model, "prompt": "ping", "stream": False,
                     "options": {"num_predict": 1}},
                )
            ms = int((time.perf_counter() - t0) * 1000)
            return TestResult(
                ok=True, message=f"Ollama connection successful：{model} 可用",
                category="ok", latency_ms=ms, model=model, provider=self.provider,
            )
        except Exception as exc:  # noqa: BLE001
            cat, msg = classify_error(exc, model)
            return TestResult(
                ok=False, message=msg, category=cat, model=model,
                provider=self.provider, error=f"{type(exc).__name__}: {exc}",
            )


def _http_json(url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise _HttpStatusError(e.code, e.read().decode("utf-8", "ignore")[:200]) from e


# -------------------------------------------------------------------- #
# Anthropic / Gemini —— 架构预留（真实协议未接入，诚实标记 NOT TESTED）
# -------------------------------------------------------------------- #
class AnthropicAdapter(ProviderAdapter):
    provider = "anthropic"
    supports_kinds = (KIND_CHAT, KIND_VISION)

    def test_connection(self, entry: ModelEntry) -> TestResult:
        guard = self._guard_kind(entry)
        if guard:
            return guard
        return TestResult(
            ok=False, message="Anthropic 协议尚未接入真实测试（NOT TESTED）。架构已预留。",
            category="not_supported", model=entry.model, provider=self.provider,
            error="anthropic_not_implemented",
        )


class GeminiAdapter(ProviderAdapter):
    provider = "gemini"
    supports_kinds = (KIND_CHAT, KIND_VISION)

    def test_connection(self, entry: ModelEntry) -> TestResult:
        guard = self._guard_kind(entry)
        if guard:
            return guard
        return TestResult(
            ok=False, message="Google Gemini 协议尚未接入真实测试（NOT TESTED）。架构已预留。",
            category="not_supported", model=entry.model, provider=self.provider,
            error="gemini_not_implemented",
        )


class StubAdapter(ProviderAdapter):
    """未知/自定义 Provider：不伪造真实测试。"""

    provider = "unknown"

    def test_connection(self, entry: ModelEntry) -> TestResult:
        return TestResult(
            ok=False, message=f"未识别或未实现该 Provider 的协议（NOT TESTED）：{entry.provider or 'unknown'}",
            category="not_supported", model=entry.model, provider=entry.provider or "unknown",
            error="provider_not_implemented",
        )


# -------------------------------------------------------------------- #
# 解析器
# -------------------------------------------------------------------- #
_ADAPTERS: Dict[str, ProviderAdapter] = {
    "openai": OpenAICompatibleAdapter(),
    "openai-compatible": OpenAICompatibleAdapter(),
    "custom": OpenAICompatibleAdapter(),
    "bigmodel": OpenAICompatibleAdapter(),  # 智谱 GLM（OpenAI-compatible）
    "deepseek": OpenAICompatibleAdapter(),  # DeepSeek（OpenAI-compatible）
    "qwen": OpenAICompatibleAdapter(),      # Qwen / DashScope（OpenAI-compatible）
    "dashscope": OpenAICompatibleAdapter(),
    "ollama": OllamaAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}


def get_provider_adapter(provider: str = "", entry: Optional[ModelEntry] = None) -> ProviderAdapter:
    """根据 provider / entry（含 base_url 推断）返回对应 Adapter；未知则返回 StubAdapter。"""
    p = (provider or "").lower().strip()
    if p in _ADAPTERS:
        return _ADAPTERS[p]
    if entry is not None:
        b = (entry.base_url or "").lower()
        if "11434" in b or "localhost" in b or "127.0.0.1" in b or "0.0.0.0" in b:
            return _ADAPTERS["ollama"]
    return StubAdapter()