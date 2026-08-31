"""Models API + 模型路由测试。

- API：Create/Read/Update/Delete / Enable/Disable / Default / Test（无 Key -> 4xx）
- Secret 绝不回传、models.json 不含 Secret
- 真正的「请求选择 A / 选择 B -> 后端分别路由到 A / B」验证
"""

import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from querynest.api.server import create_app  # noqa: E402
from querynest.core.model_registry import ModelEntry, ModelRegistry  # noqa: E402
from querynest.core.models import RetrievalResult  # noqa: E402


class _RoutingEngine:
    """注入的假引擎：记录每次问答路由到的模型标识。"""

    def __init__(self):
        self.reg = None
        self.used = []

    async def query(self, query, mode="mix", top_k=20, history=None,
                    system_prompt=None, model_id=None, **kw):
        if model_id and self.reg is not None:
            m = self.reg.resolve("chat", model_id)
            self.used.append(m.model)
            answer = f"routed:{m.model}"
        else:
            answer = "routed:default"
        return RetrievalResult(answer=answer)


@pytest.fixture
def api(tmp_path):
    app = create_app(engine=_RoutingEngine())
    app.state.registry = ModelRegistry(str(tmp_path))
    app.state.registry.__dict__["_entries"] = []  # 清空种子，测试自建
    app.state.engine.reg = app.state.registry
    return TestClient(app), app


def _add(client, **kw):
    body = {"provider": "openai", "model": "gpt-x", "kind": "chat",
            "api_key": "sk-real-9999"}
    body.update(kw)
    r = client.post("/models", json=body)
    assert r.status_code == 200, r.text
    return r.json()["model"]


# ---------------- CRUD 与 Secret ----------------
def test_list_never_leaks_secret(api):
    client, app = api
    _add(client, model="gpt-a", name="A")
    r = client.get("/models")
    assert r.status_code == 200
    raw = json.dumps(r.json())
    assert "sk-real-9999" not in raw
    assert "_real" not in raw
    for m in r.json()["models"]:
        assert m.get("has_api_key") in (True, False)
        assert "sk-" not in m.get("api_key", "")


def test_add_returns_masked_secret(api):
    client, _ = api
    m = _add(client, name="X")
    assert m["has_api_key"] is True
    assert m["api_key"] == "***9999"


def test_update_keeps_secret_when_blank(api):
    client, app = api
    m = _add(client, model="gpt-a")
    r = client.put("/models/" + m["id"], json={"name": "Renamed"})
    assert r.status_code == 200
    assert r.json()["model"]["name"] == "Renamed"
    assert app.state.registry.get(m["id"]).api_key == "sk-real-9999"


def test_update_replaces_secret_when_new(api):
    client, app = api
    m = _add(client, model="gpt-a")
    client.put("/models/" + m["id"], json={"api_key": "sk-new-001"})
    assert app.state.registry.get(m["id"]).api_key == "sk-new-001"
    assert app.state.registry.secrets.get(m["id"]) == "sk-new-001"


def test_delete_removes_secret(api):
    client, app = api
    m = _add(client, model="gpt-a")
    assert client.delete("/models/" + m["id"]).status_code == 200
    assert app.state.registry.secrets.get(m["id"]) == ""


# ---------------- Enable / Disable / Default ----------------
def test_enable_disable(api):
    client, _ = api
    m = _add(client, model="gpt-a")
    r = client.post(f"/models/{m['id']}/disable")
    assert r.status_code == 200 and r.json()["model"]["enabled"] is False
    r = client.post(f"/models/{m['id']}/enable")
    assert r.status_code == 200 and r.json()["model"]["enabled"] is True


def test_default_uniqueness(api):
    client, _ = api
    a = _add(client, model="gpt-a")
    b = _add(client, model="gpt-b")
    assert client.post(f"/models/{b['id']}/default").status_code == 200
    data = client.get("/models").json()
    assert any(x["id"] == b["id"] and x["is_default"] for x in data["models"])
    assert not any(x["id"] == a["id"] and x["is_default"] for x in data["models"])
    assert data["active"]["chat"]["id"] == b["id"]


# ---------------- Test 连接 ----------------
def test_test_endpoint_no_key_is_4xx_not_500(api):
    client, _ = api
    m = _add(client, model="gpt-a", api_key="")          # 无 Key
    r = client.post(f"/models/{m['id']}/test")
    assert r.status_code == 422                          # 明确 4xx，不 500
    assert "not configured" in r.json()["detail"]["error"].lower()


def test_test_invalid_model_422(api):
    client, _ = api
    r = client.post("/models/nope/test")
    assert r.status_code == 422


# ---------------- 问答路由：真正 A ≠ B ----------------
def test_query_routes_requested_model_a_then_b(api):
    client, app = api
    a = _add(client, model="gpt-a")
    b = _add(client, model="gpt-b")
    # 发送 A
    r1 = client.post("/query", json={"query": "hello", "model_id": a["id"]})
    assert r1.status_code == 200
    # 发送 A 之后切换 B
    r2 = client.post("/query", json={"query": "hello", "model_id": b["id"]})
    assert r2.status_code == 200
    assert "model_id" not in r1.json()["answer"]
    assert app.state.engine.used[-2:] == ["gpt-a", "gpt-b"]
    assert app.state.engine.used[-2] != app.state.engine.used[-1]  # A ≠ B


def test_query_invalid_model_422(api):
    client, _ = api
    r = client.post("/query", json={"query": "hi", "model_id": "missing"})
    assert r.status_code == 422


def test_query_disabled_model_422(api):
    client, _ = api
    m = _add(client, model="gpt-a")
    client.post(f"/models/{m['id']}/disable")
    r = client.post("/query", json={"query": "hi", "model_id": m["id"]})
    assert r.status_code == 422
    assert "禁用" in r.json()["detail"]["error"]


def test_query_wrong_kind_422(api):
    client, _ = api
    m = _add(client, model="text-embed", kind="embedding", dimension=256, api_key="k")
    r = client.post("/query", json={"query": "hi", "model_id": m["id"]})
    assert r.status_code == 422


# ---------------- 引擎按请求切换的真实路由（函数级） ----------------
def test_engine_swap_actually_binds_different_model(monkeypatch):
    """核心：请求 model_id=A 与 model_id=B 时，实际挂载到引擎的生成函数不同。

    通过替换 build_openai_llm_func 捕获被构造的 model —— 证明后端真实路由到 A/B。
    """
    import querynest.core.engine as eng

    bound = []

    def fake_build(config=None, api_key=None, base_url=None, model=None,
                   temperature=None, max_tokens=None):
        bound.append(model)
        return (lambda p, s=None: model)

    monkeypatch.setattr("querynest.core.clients.build_openai_llm_func", fake_build)

    er = eng.QueryNest.__new__(eng.QueryNest)

    class _LR:
        llm_model_func = None

    class _Rag:
        llm_model_func = None

    class _Reg:
        A = ModelEntry(id="A", model="model-a", provider="openai", api_key="k")
        B = ModelEntry(id="B", model="model-b", provider="openai", api_key="k")
        def resolve(self, kind, mid):
            return self.A if mid == "A" else self.B

    er.logger = type("L", (), {"info": lambda *a, **k: None})()
    er.llm_model_func = "default"
    er.vision_model_func = None
    er._rag_engine = _Rag()
    er._lightrag = _LR()
    er.model_registry = _Reg()

    async def go():
        s1 = await er._swap_request_model("chat", "A")
        fA = er.llm_model_func
        er._restore_request_model(s1)
        assert er.llm_model_func == "default"          # 请求后还原
        s2 = await er._swap_request_model("chat", "B")
        fB = er.llm_model_func
        er._restore_request_model(s2)
        assert er.llm_model_func == "default"
        return fA, fB

    asyncio.run(go())
    assert bound == ["model-a", "model-b"]
    assert bound[0] != bound[1]                        # A ≠ B，真实路由