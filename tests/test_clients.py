"""core.clients 构造逻辑测试（纯离线，不发起真实网络请求）。"""

import os

import pytest

from querynest.core.clients import (
    build_openai_embedding_func,
    build_openai_llm_func,
    build_vision_model_func,
    load_env,
)


def test_load_env_sets_missing_only(tmp_path, monkeypatch):
    d = tmp_path / "env"
    d.write_text(
        '# 注释\n\nQUERYNEST_TESTING_UNIQUE_MODEL=my-model\nQUERYNEST_TESTING_QUOTED="quoted"\n'
        "QUERYNEST_TESTING_EXISTING=should_not_override\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("QUERYNEST_TESTING_EXISTING", "already")
    load_env(str(d))
    assert os.environ.get("QUERYNEST_TESTING_UNIQUE_MODEL") == "my-model"
    assert os.environ.get("QUERYNEST_TESTING_QUOTED") == "quoted"
    # 已存在的变量不会被 .env 覆盖（幂等）
    assert os.environ.get("QUERYNEST_TESTING_EXISTING") == "already"


def test_load_env_missing_file(tmp_path):
    load_env(str(tmp_path / "nope.env"))  # 不应抛异常


def test_llm_func_requires_api_key(monkeypatch):
    monkeypatch.delenv("QUERYNEST_LLM_API_KEY", raising=False)
    with pytest.raises(ValueError):
        build_openai_llm_func(api_key="")


def test_llm_func_returns_callable(monkeypatch):
    monkeypatch.setenv("QUERYNEST_LLM_API_KEY", "sk-test-dummy-key")
    fn = build_openai_llm_func(api_key="sk-test-dummy-key")
    assert callable(fn)


def test_embedding_func_returns_callable(monkeypatch):
    monkeypatch.setenv("QUERYNEST_LLM_API_KEY", "sk-test-dummy-key")
    fn = build_openai_embedding_func(api_key="sk-test-dummy-key")
    assert callable(fn)


def test_vision_func_requires_api_key(monkeypatch):
    monkeypatch.delenv("QUERYNEST_LLM_API_KEY", raising=False)
    with pytest.raises(ValueError):
        build_vision_model_func(api_key="")


def test_vision_func_returns_callable(monkeypatch):
    monkeypatch.setenv("QUERYNEST_LLM_API_KEY", "sk-test-dummy-key")
    fn = build_vision_model_func(api_key="sk-test-dummy-key")
    assert callable(fn)


def test_vision_func_forwards_multimodal_messages(monkeypatch):
    """多模态调用须把完整 OpenAI messages（含 image_url）直传给 chat.create，
    模型名取 QUERYNEST_VISION_MODEL。"""
    from querynest import QueryNestConfig

    monkeypatch.setenv("QUERYNEST_LLM_API_KEY", "sk-test-dummy-key")
    monkeypatch.setenv("QUERYNEST_VISION_MODEL", "glm-4v-flash")

    cfg = QueryNestConfig()
    fn = build_vision_model_func(cfg, api_key="sk-test-dummy-key")
    client = fn._client_factory()
    captured = {}

    class _Msg:
        content = "chart描述"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    def fake_create(**kwargs):
        captured.update(kwargs)
        return _Resp()

    client.chat.completions.create = fake_create

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [
            {"type": "text", "text": "趋势?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
        ]},
    ]
    out = fn("", messages=messages)
    assert out == "chart描述"
    assert captured["model"] == "glm-4v-flash"
    assert captured["messages"][1]["content"][1]["type"] == "image_url"
    # glm-4v-flash 上限 1024，默认不应把共享 llm_max_tokens(8192) 带上
    assert "max_tokens" not in captured