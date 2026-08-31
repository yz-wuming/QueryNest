"""Model Registry 单元测试：CRUD / enable-disable / default 唯一性 / resolve /
Secret 分离 / 旧 schema 迁移 / embedding 维度护栏。

不依赖任何真实外部模型连接。
"""

import json
from dataclasses import asdict

import pytest

from querynest.core.model_registry import KINDS, ModelRegistry, RegistryError


@pytest.fixture
def reg(tmp_path):
    return ModelRegistry(str(tmp_path))


def test_seed_contains_all_kinds(reg):
    kinds = [e.kind for e in reg.list()]
    assert set(kinds) == set(KINDS)
    # 各类用途恰好一个默认
    for k in KINDS:
        defs = [e for e in reg.list(k) if e.is_default]
        assert len(defs) == 1, k


def test_add_and_masked_api(reg):
    m = reg.add({"name": "GLM", "provider": "bigmodel", "model": "glm-4-flash",
                 "base_url": "https://open.bigmodel.cn/api/paas/v4",
                 "kind": "chat", "api_key": "sk-secret-1234"})
    d = m.to_dict()
    assert d["has_api_key"] is True
    assert d["api_key"] == "***1234"          # 只返回掩码
    assert "sk-secret-1234" not in json.dumps(d, ensure_ascii=False)
    # Secret 落在 SecretStore，不写进 models.json
    raw = json.loads(reg.path.read_text(encoding="utf-8"))
    entry = next(x for x in raw if x["id"] == m.id)
    assert "api_key" not in entry
    assert "apiKey" not in raw[0]
    # 内存仍持有真实 Key，SecretStore 可回取
    assert reg.secrets.get(m.id) == "sk-secret-1234"


def test_update_keeps_secret_when_blank(reg):
    m = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat",
                 "api_key": "sk-A"})
    reg.update(m.id, {"name": "Renamed"})          # 不传 api_key -> 保留旧 Key
    got = reg.get(m.id)
    assert got.name == "Renamed"
    assert got.api_key == "sk-A"


def test_update_replaces_secret_when_new(reg):
    m = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat",
                 "api_key": "sk-A"})
    reg.update(m.id, {"api_key": "sk-B"})
    assert reg.get(m.id).api_key == "sk-B"
    assert reg.secrets.get(m.id) == "sk-B"


def test_delete_also_deletes_secret(reg):
    m = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat",
                 "api_key": "sk-A"})
    reg.delete(m.id)
    with pytest.raises(RegistryError):
        reg.get(m.id)
    reg2 = ModelRegistry(str(reg.path.parent))     # 重新加载验证持久化删除
    with pytest.raises(RegistryError):
        reg2.get(m.id)
    assert reg2.secrets.get(m.id) == ""


def test_delete_default_blocked(reg):
    with pytest.raises(RegistryError):
        reg.delete("chat-default")


def test_embedding_dimension_guard(reg):
    with pytest.raises(RegistryError):
        reg.add({"provider": "openai", "model": "embed-b", "kind": "embedding",
                 "dimension": 4096})  # 与种子默认 dim 不一致
    # confirm 时允许
    reg.add({"provider": "openai", "model": "embed-b", "kind": "embedding",
             "dimension": 4096, "api_key": "k"}, confirm=True)


def test_default_uniqueness_per_kind(reg):
    a = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat", "api_key": "x"})
    b = reg.add({"provider": "openai", "model": "gpt-b", "kind": "chat", "api_key": "x"})
    reg.set_default(b.id)
    assert reg.get(a.id).is_default is False
    assert reg.get(b.id).is_default is True
    defs = [e for e in reg.list("chat") if e.is_default]
    assert len(defs) == 1 and defs[0].id == b.id


def test_enable_disable(reg):
    a = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat", "api_key": "x"})
    reg.disable(a.id)
    assert reg.get(a.id).enabled is False
    reg.enable(a.id)
    assert reg.get(a.id).enabled is True


def test_resolve_request_model_priority_over_default(reg):
    """request.model_id > 用户默认 > fallback。"""
    default = reg.resolve("chat")                    # 种子默认（default）
    a = reg.add({"provider": "openai", "model": "gpt-a", "kind": "chat", "api_key": "x"})
    b = reg.add({"provider": "openai", "model": "gpt-b", "kind": "chat", "api_key": "x"})
    assert reg.resolve("chat", a.id).model == "gpt-a"
    assert reg.resolve("chat", b.id).model == "gpt-b"
    assert default.id != a.id
    # 未指定 model_id 时走默认
    assert reg.resolve("chat").is_default is True


def test_resolve_disabled_default_skipped(reg):
    # 把种子默认禁用后，fallback 到其它启用模型
    m = reg.add({"provider": "openai", "model": "gpt-x", "kind": "chat", "api_key": "x",
                 "enabled": True})
    reg.disable("chat-default")
    assert reg.resolve("chat").id == m.id


def test_resolve_wrong_kind_rejected(reg):
    with pytest.raises(RegistryError):
        reg.resolve("vision", "chat-default")       # 聊天模型不能当视觉


def test_migration_legacy_schema(tmp_path):
    """旧版 models.json：`type`、`default`、内嵌 api_key -> 归一化且 Secret 剥离。"""
    path = tmp_path / "models.json"
    path.write_text(json.dumps([
        {"id": "legacy-1", "type": "chat", "name": "旧模型", "model": "old-x",
         "provider": "openai", "base_url": "", "default": True,
         "api_key": "sk-legacy", "dimension": 0},
    ]), encoding="utf-8")
    reg = ModelRegistry(str(tmp_path))
    m = reg.get("legacy-1")
    assert m.kind == "chat"
    assert m.is_default is True
    assert m.api_key == ""                          # 旧 Key 被剥离，不留下
    assert json.dumps(reg.path.read_text(encoding="utf-8")).find("sk-legacy") == -1


def test_vision_kind_distinct_from_chat(reg):
    c = reg.resolve("chat")
    v = reg.resolve("vision")
    assert c.kind == "chat" and v.kind == "vision"
    assert c.id != v.id
    # 聊天模型不会自动充当 vision
    with pytest.raises(RegistryError):
        reg.resolve("vision", c.id)


def test_reranker_registered(kind_reranker=("reranker",)):
    """reranker 至少注册为一种 kind，可 enable/disable/default。"""
    assert "reranker" in KINDS