"""
QueryNest 模型客户端工具

从 ``QueryNestConfig``（``QUERYNEST_*`` 环境变量）构造 OpenAI-compatible 的
LLM / Embedding 回调，供真实端到端运行使用。

- LLM 回调签名（与 LightRAG 兼容）：``func(prompt: str, system_prompt: str | None = None) -> str``
- Embedding 回调签名（与 LightRAG 兼容）：``func(texts: list[str]) -> numpy.ndarray``

所有模块级构建函数均为**延迟导入 openai**，因此本模块在未安装 openai 时也能
被安全导入（真实调用才会失败）。Embedding 附带轻量内存缓存，避免重复调用远程
接口浪费 token。

纯 Python + 轻依赖；可用 mock 离线单测构造逻辑，不强制真实网络调用。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional

from querynest.core.config import QueryNestConfig


def load_env(dotenv_path: Optional[str] = None) -> None:
    """极简 .env 加载器（无第三方依赖），仅设置当前环境未定义的变量。

    兼容 ``KEY=VALUE``、空行、``#`` 注释；不做变量替换。结果是幂等的。
    """
    path = dotenv_path or os.environ.get("QUERYNEST_DOTENV") or ".env"
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


def build_openai_llm_func(
    config: Optional[QueryNestConfig] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Callable[[str, Optional[str]], str]:
    """构造 OpenAI-compatible ChatCompletion 回调。

    参数缺省时从 ``config`` 读取（对应 ``QUERYNEST_LLM_*`` 环境变量）；再缺省回退
    到默认值。未配置 API Key 时抛 ``ValueError``，避免静默使用错误凭据。
    """
    real_openai = _import_openai()
    cfg = config or QueryNestConfig()
    api_key = api_key or cfg.llm_api_key or ""
    if not api_key:
        raise ValueError(
            "缺少 LLM API Key。请设置 QUERYNEST_LLM_API_KEY（或在 .env 中）后重试。"
        )
    base_url = base_url or cfg.llm_base_url
    model = model or cfg.llm_model
    temperature = cfg.llm_temperature if temperature is None else temperature
    max_tokens = max_tokens or cfg.llm_max_tokens

    client = real_openai.OpenAI(api_key=api_key, base_url=base_url)

    def _llm(prompt: str, system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        del kwargs  # 兼容 LightRAG 传入的额外参数（如 hashing_kv 等）
        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        try:
            return resp.choices[0].message.content or ""
        except (AttributeError, IndexError):
            return str(resp)

    _llm._client_factory = lambda: client  # 便于测试/自省
    return _llm


def build_openai_embedding_func(
    config: Optional[QueryNestConfig] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    cache: bool = True,
) -> Callable[[List[str]], Any]:
    """构造 OpenAI-compatible Embedding 回调，返回 numpy float32 数组。

    与 LightRAG 的 ``embedding_func(texts: list[str]) -> np.ndarray`` 一致。
    参数缺省时从 ``config``（``QUERYNEST_EMBEDDING_*`` / ``QUERYNEST_LLM_*`` 环境变量）读取。
    """
    np = _import_numpy()
    real_openai = _import_openai()
    cfg = config or QueryNestConfig()
    api_key = api_key or cfg.embedding_binding_api_key or cfg.llm_api_key or ""
    if not api_key:
        raise ValueError(
            "缺少 Embedding/LLM API Key。请设置 QUERYNEST_LLM_API_KEY "
            "（或 QUERYNEST_EMBEDDING_BINDING_API_KEY）后重试。"
        )
    base_url = base_url or cfg.llm_base_url
    model = model or cfg.embedding_model

    client = real_openai.OpenAI(api_key=api_key, base_url=base_url)
    _cache: Dict[str, List[float]] = {}

    def _embed(texts: List[str], **kwargs: Any) -> Any:
        del kwargs  # 兼容 LightRAG 可能注入的额外参数
        unique = list(dict.fromkeys(t for t in texts if t))
        if cache:
            missing = [t for t in unique if t not in _cache]
            if missing:
                _fetch(client, model, missing, _cache)
            vecs = [_cache[t] for t in unique]
        else:
            vecs = _fetch(client, model, unique, {})
        return np.asarray(vecs, dtype="float32")

    _embed._client_factory = lambda: client  # 便于测试/自省
    return _embed


def build_vision_model_func(
    config: Optional[QueryNestConfig] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
) -> Callable[..., str]:
    """构造 OpenAI-compatible VLM（视觉）回调，复用现有 LLM 的 base_url 与 API Key。

    与 ``build_openai_llm_func`` 同风格。模型名取 ``config.vision_model``
    （``QUERYNEST_VISION_MODEL``）。签名兼容 ``query/base.py`` 中 ``vision_model_func``
    的两种调用方式：

    - ``func(prompt, system_prompt=...)`` ── 纯文本
    - ``func("", messages=[system, {"role":"user","content":[text,image_url...]}])`` ── 多模态

    未配置 API Key 时抛 ``ValueError``，避免静默使用错误凭据。
    """
    real_openai = _import_openai()
    cfg = config or QueryNestConfig()
    api_key = api_key or cfg.llm_api_key or ""
    if not api_key:
        raise ValueError(
            "缺少视觉模型 API Key。请设置 QUERYNEST_LLM_API_KEY（或在 .env 中）后重试。"
        )
    base_url = base_url or cfg.llm_base_url
    model = model or cfg.vision_model or cfg.llm_model
    temperature = cfg.llm_temperature if temperature is None else temperature
    # 视觉模型（如 glm-4v-flash）的 max_tokens 上限远小于文本 LLM（默认 8192），
    # 直接沿用共享的 llm_max_tokens 会触发 400。默认交给模型自身默认值，
    # 仅在显式传入时使用。
    vision_max_tokens = max_tokens if max_tokens is not None else None

    client = real_openai.OpenAI(api_key=api_key, base_url=base_url)

    def _vision(prompt: str = "", system_prompt: Optional[str] = None, **kwargs: Any) -> str:
        messages = kwargs.pop("messages", None)
        if messages is None:
            msgs: List[Dict[str, Any]] = []
            if system_prompt:
                msgs.append({"role": "system", "content": system_prompt})
            msgs.append({"role": "user", "content": prompt or ""})
        else:
            # 多模态：直接使用调用方已构造好的 OpenAI messages（含 image_url）
            msgs = messages
        create_kwargs: Dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
        }
        if vision_max_tokens is not None:
            create_kwargs["max_tokens"] = vision_max_tokens
        resp = client.chat.completions.create(**create_kwargs)
        try:
            return resp.choices[0].message.content or ""
        except (AttributeError, IndexError):
            return str(resp)

    _vision._client_factory = lambda: client  # 便于测试/自省
    return _vision


def _fetch(client: Any, model: str, texts: List[str], cache: Dict[str, List[float]]) -> List[List[float]]:
    resp = client.embeddings.create(model=model, input=texts)
    order = {d.index: d.embedding for d in resp.data}
    out = [order.get(i, []) for i in range(len(texts))]
    for t, v in zip(texts, out):
        if v:
            cache[t] = v
    return out


def _import_openai() -> Any:
    try:
        import openai as oa
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "使用 OpenAI-compatible 回调需要安装 openai：`pip install openai`"
        ) from e
    return oa


def _import_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("embedding 回调需要 numpy：`pip install numpy`") from e
    return np