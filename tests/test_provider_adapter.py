"""Provider Adapter 单元测试：适配器解析、真实路径分类、错误归类。

用 mock 验证 OpenAI/Ollama 路径的「正确 base_url + model + kind 被调用」与错误分类；
真实外部连通性连接不做（无 Key）—— 标记 NOT TESTED。
"""

import pytest

from querynest.core.model_registry import ModelEntry
from querynest.core.providers import (
    AnthropicAdapter,
    GeminiAdapter,
    OllamaAdapter,
    OpenAICompatibleAdapter,
    StubAdapter,
    classify_error,
    get_provider_adapter,
)


def _entry(**kw):
    base = {"id": "x", "provider": "openai", "model": "gpt-x", "kind": "chat",
            "base_url": "", "api_key": ""}
    base.update(kw)
    return ModelEntry(**base)


# ---------------- 适配器解析 ----------------
def test_adapter_mapping():
    assert isinstance(get_provider_adapter("openai"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("deepseek"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("qwen"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("bigmodel"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("dashscope"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("custom"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("openai-compatible"), OpenAICompatibleAdapter)
    assert isinstance(get_provider_adapter("ollama"), OllamaAdapter)
    assert isinstance(get_provider_adapter("anthropic"), AnthropicAdapter)
    assert isinstance(get_provider_adapter("gemini"), GeminiAdapter)
    assert isinstance(get_provider_adapter("nope"), StubAdapter)


def test_ollama_inferred_from_localhost():
    a = get_provider_adapter("", _entry(provider="", base_url="http://127.0.0.1:11434"))
    assert isinstance(a, OllamaAdapter)


# ---------------- OpenAI 兼容（mock 验证真实调用与分类） ----------------
def test_openai_no_key_not_configured(monkeypatch):
    res = OpenAICompatibleAdapter().test_connection(_entry())
    assert res.ok is False
    assert res.category == "not_configured"
    assert res.message == "API key is not configured"


def test_openai_reranker_not_supported():
    res = OpenAICompatibleAdapter().test_connection(_entry(kind="reranker", api_key="k"))
    assert res.category == "not_supported"


def test_openai_invalid_kind():
    res = OpenAICompatibleAdapter().test_connection(
        _entry(kind="??", api_key="k"))
    assert res.category == "invalid_kind"


def _patch_openai(monkeypatch, status=None, success=True, exc=None):
    import openai as oa  # providers 内是局部 import openai，这里直接补丁真模块
    captured = {}

    class FakeCompletions:
        def create(self, **kw):
            captured["create"] = kw
            if exc:
                raise exc
            if status and status >= 400:
                e = oa.APIStatusError.new(  # type: ignore
                    body={}, raw="err", status_code=status, message="err", headers={}, request=None)
                raise e
            if not success:
                raise Exception("network down")
            return type("R", (), {"choices": []})()

    class FakeEmbeddings:
        def create(self, **kw):
            captured["create"] = kw
            if exc:
                raise exc
            return type("R", (), {"data": []})

    class FakeClient:
        def __init__(self, *a, **k):
            captured["client_kwargs"] = k
        chat = type("chat", (), {"completions": FakeCompletions()})()
        embeddings = type("emb", (), {"create": FakeEmbeddings().create})()

    monkeypatch.setattr(oa, "OpenAI", FakeClient)
    monkeypatch.setattr(oa, "APIStatusError", oa.APIStatusError)
    return captured


def test_openai_chat_real_test(monkeypatch):
    captured = _patch_openai(monkeypatch)
    res = OpenAICompatibleAdapter().test_connection(
        _entry(provider="deepseek", base_url="https://api.deepseek.com",
               model="deepseek-chat", api_key="sk-x"))
    assert res.ok is True
    assert res.category == "ok"
    # 确认走得是真实 provider + 正确 base_url + model
    assert captured["client_kwargs"]["base_url"] == "https://api.deepseek.com"
    assert captured["create"]["model"] == "deepseek-chat"
    assert captured["create"]["messages"][0]["content"] == "ping"


def test_openai_embedding_uses_embeddings_endpoint(monkeypatch):
    captured = _patch_openai(monkeypatch)
    res = OpenAICompatibleAdapter().test_connection(
        _entry(kind="embedding", model="text-embed", api_key="k"))
    assert res.ok is True
    assert captured["create"]["input"] == "ping"


def test_error_classification_401():
    class E(Exception):
        status_code = 401
    cat, msg = classify_error(E())
    assert cat == "auth"


def test_error_classification_404():
    class E(Exception):
        status_code = 404
    cat, _ = classify_error(E())
    assert cat == "not_found"


def test_error_classification_429():
    class E(Exception):
        status_code = 429
    cat, _ = classify_error(E())
    assert cat == "rate_limit"


def test_error_classification_500():
    class E(Exception):
        status_code = 500
    cat, _ = classify_error(E())
    assert cat == "server_error"


def test_error_classification_timeout():
    cat, _ = classify_error(TimeoutError("timed out"))
    assert cat == "timeout"


def test_error_classification_network():
    cat, _ = classify_error(ConnectionError("network unreachable"))
    assert cat == "network"


def test_error_classification_invalid_model():
    cat, _ = classify_error(RuntimeError("model not found: gpt-x"), model="gpt-x")
    assert cat == "invalid_model"


# ---------------- Ollama ----------------
def test_ollama_requires_no_key():
    assert OllamaAdapter().requires_api_key is False
    res = OllamaAdapter().test_connection(_entry(provider="ollama", model="qwen", api_key=""))
    # 无 Key 应该继续走到网络（本地），失败原因是网络/未找到，而不是 not_configured
    assert res.category != "not_configured"
    assert res.ok is False  # 无本地服务 -> 连接失败（真实环境 NOT TESTED）


def test_ollama_default_base_url():
    assert OllamaAdapter()._base(_entry(provider="ollama", base_url="")) == "http://localhost:11434"
    assert OllamaAdapter()._base(_entry(provider="ollama", base_url="http://192.168.1.5:8080/")) \
        == "http://192.168.1.5:8080"


def test_ollama_reranker_not_supported():
    res = OllamaAdapter().test_connection(_entry(provider="ollama", kind="reranker", model="q"))
    assert res.category == "not_supported"


# ---------------- 预留 Provider ----------------
def test_anthropic_stub_not_supported():
    res = AnthropicAdapter().test_connection(_entry(provider="anthropic", model="claude", api_key="k"))
    assert res.category == "not_supported"


def test_gemini_stub_not_supported():
    res = GeminiAdapter().test_connection(_entry(provider="gemini", model="gemini", api_key="k"))
    assert res.category == "not_supported"


def test_stub_unknown_provider():
    res = StubAdapter().test_connection(_entry(provider="weird", model="x", api_key="k"))
    assert res.category == "not_supported"