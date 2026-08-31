"""Conversation & Chat History 测试。

覆盖：
- API：create / list / get / rename / delete / messages / 404 / 空标题 / 非法会话
- 消息：持久化 / message_count / updated_at / model_id / sources / trace_id
- 存储：空仓库 / 增删改查 / JSON 持久化 / 重启模拟 / 损坏 JSON / 缺失消息文件
- 向后兼容：POST /query 仍然可用
"""

import json
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from querynest.api.server import create_app  # noqa: E402
from querynest.core.exceptions import ConversationNotFoundError  # noqa: E402
from querynest.core.models import Citation, RetrievalResult  # noqa: E402
from querynest.storage.conversation_store import (  # noqa: E402
    ConversationStore,
    make_title,
)


class _FakeEngine:
    """轻量假引擎：返回带 sources / trace_id 的稳定结果，避免引入 lightrag。"""

    def __init__(self):
        self.calls = []

    async def query(self, query, mode="mix", top_k=20, history=None,
                    system_prompt=None, model_id=None, **kw):
        self.calls.append({"query": query, "mode": mode, "top_k": top_k,
                           "model_id": model_id})
        return RetrievalResult(
            answer=f"answer for {query}",
            sources=[
                Citation(document_name="paper.pdf", page=2, content_type="table",
                         score=0.91, text="benchmark table"),
                Citation(document_name="report.pdf", page=5, content_type="text",
                         score=0.82, text="retrieval benchmark"),
            ],
            retrieval={"num_hits": 2, "mode": mode},
            metadata={"trace_id": "trace-fake-001"},
        )

    async def query_multimodal(self, query, multimodal_content=None, mode="mix",
                               system_prompt=None, model_id=None, **kw):
        self.calls.append({"query": query, "mode": "multimodal",
                           "model_id": model_id})
        return RetrievalResult(
            answer="multimodal answer",
            sources=[Citation(document_name="chart.png", content_type="image",
                              score=0.9)],
            retrieval={"num_hits": 1, "multimodal": True},
            metadata={"trace_id": "trace-fake-mm"},
        )


@pytest.fixture
def api(tmp_path):
    from querynest.core.model_registry import ModelRegistry

    app = create_app(engine=_FakeEngine(),
                     conversation_store=ConversationStore(str(tmp_path / "store")))
    app.state.registry = ModelRegistry(str(tmp_path / "reg"))
    client = TestClient(app)
    # 注册一个聊天模型，供 model_id 绑定测试使用
    r = client.post("/models", json={
        "provider": "openai", "model": "gpt-x", "kind": "chat",
        "api_key": "sk-test-0000", "name": "测试模型",
    })
    assert r.status_code == 200, r.text
    return client, app


@pytest.fixture
def client(api):
    return api[0]


# ---------------- Conversation CRUD ----------------
def test_create_conversation(client):
    r = client.post("/conversations", json={})
    assert r.status_code == 200, r.text
    c = r.json()["conversation"]
    assert c["id"].startswith("c-")
    assert c["title"] == ""
    assert c["message_count"] == 0
    assert c["retrieval_mode"] == "mix"


def test_create_with_metadata(client):
    r = client.post("/conversations", json={
        "title": "PDF 分析",
        "model_id": "m-1",
        "retrieval_mode": "hybrid",
        "document_ids": ["d1", "d2"],
    })
    c = r.json()["conversation"]
    assert c["title"] == "PDF 分析"
    assert c["model_id"] == "m-1"
    assert c["retrieval_mode"] == "hybrid"
    assert c["document_ids"] == ["d1", "d2"]


def test_list_conversations(client):
    a = client.post("/conversations", json={"title": "A"}).json()["conversation"]
    b = client.post("/conversations", json={"title": "B"}).json()["conversation"]
    r = client.get("/conversations")
    ids = [c["id"] for c in r.json()["conversations"]]
    assert a["id"] in ids and b["id"] in ids


def test_get_conversation(client):
    c = client.post("/conversations", json={"title": "X"}).json()["conversation"]
    r = client.get("/conversations/" + c["id"])
    assert r.status_code == 200
    assert r.json()["conversation"]["title"] == "X"


def test_get_conversation_404(client):
    r = client.get("/conversations/nope-000")
    assert r.status_code == 404


def test_rename_conversation(client):
    c = client.post("/conversations", json={"title": "旧标题"}).json()["conversation"]
    r = client.patch("/conversations/" + c["id"], json={"title": "  新标题  "})
    assert r.status_code == 200
    assert r.json()["conversation"]["title"] == "新标题"  # trim


def test_rename_empty_title_rejected(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.patch("/conversations/" + c["id"], json={"title": "   "})
    assert r.status_code == 422


def test_rename_overlong_title_rejected(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.patch("/conversations/" + c["id"], json={"title": "x" * 101})
    assert r.status_code == 422


def test_rename_404(client):
    r = client.patch("/conversations/nope-000", json={"title": "x"})
    assert r.status_code == 404


def test_delete_conversation(client):
    c = client.post("/conversations", json={"title": "删我"}).json()["conversation"]
    r = client.delete("/conversations/" + c["id"])
    assert r.status_code == 200
    assert client.get("/conversations/" + c["id"]).status_code == 404


def test_delete_conversation_404(client):
    r = client.delete("/conversations/nope-000")
    assert r.status_code == 404


# ---------------- Messages ----------------
def test_add_message_persists_user_and_assistant(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages",
                    json={"content": "分析一下 PDF 检索性能"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_message"]["role"] == "user"
    assert body["user_message"]["content"] == "分析一下 PDF 检索性能"
    assert body["assistant_message"]["role"] == "assistant"
    assert body["assistant_message"]["content"] == "answer for 分析一下 PDF 检索性能"


def test_message_sources_from_real_retrieval(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages",
                    json={"content": "哪个模型最好？"})
    am = r.json()["assistant_message"]
    assert len(am["sources"]) == 2
    assert am["sources"][0]["document_name"] == "paper.pdf"
    assert am["sources"][0]["page"] == 2
    assert am["sources"][0]["score"] == 0.91


def test_message_trace_id_associated(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages",
                    json={"content": "trace 测试"})
    am = r.json()["assistant_message"]
    assert am["trace_id"] == "trace-fake-001"


def test_message_model_id_associated(client):
    mid = client.get("/models").json()["models"][0]["id"]  # 注册的聊天模型
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages",
                    json={"content": "模型绑定", "model_id": mid})
    am = r.json()["assistant_message"]
    assert am["model_id"] == mid
    assert r.json()["conversation"]["model_id"] == mid


def test_first_message_auto_title(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    client.post(f"/conversations/{c['id']}/messages",
                json={"content": "请分析 multimodal_test_report.pdf 中的 Retrieval Benchmark"})
    conv = client.get("/conversations/" + c["id"]).json()["conversation"]
    assert conv["title"] != ""
    assert "\n" not in conv["title"]
    assert len(conv["title"]) <= 48


def test_message_count_and_updated_at(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    t0 = c["updated_at"]
    time.sleep(1.1)
    client.post(f"/conversations/{c['id']}/messages", json={"content": "第一问"})
    conv = client.get("/conversations/" + c["id"]).json()["conversation"]
    assert conv["message_count"] == 2  # user + assistant
    assert conv["updated_at"] >= t0


def test_get_messages(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    client.post(f"/conversations/{c['id']}/messages", json={"content": "你好"})
    r = client.get(f"/conversations/{c['id']}/messages")
    msgs = r.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["role"] == "assistant"


def test_messages_404_for_unknown_conversation(client):
    r = client.get("/conversations/nope-000/messages")
    assert r.status_code == 404
    r = client.post("/conversations/nope-000/messages", json={"content": "x"})
    assert r.status_code == 404


def test_empty_message_rejected(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages", json={"content": "   "})
    assert r.status_code == 422


def test_delete_message(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    client.post(f"/conversations/{c['id']}/messages", json={"content": "删消息"})
    msgs = client.get(f"/conversations/{c['id']}/messages").json()["messages"]
    mid = msgs[0]["id"]
    r = client.delete(f"/conversations/{c['id']}/messages/{mid}")
    assert r.status_code == 200
    after = client.get(f"/conversations/{c['id']}/messages").json()["messages"]
    assert len(after) == 1
    assert client.get(f"/conversations/{c['id']}").json()["conversation"]["message_count"] == 1


def test_multimodal_message(client):
    c = client.post("/conversations", json={}).json()["conversation"]
    r = client.post(f"/conversations/{c['id']}/messages",
                    json={"content": "这是什么图？", "mode": "multimodal",
                          "multimodal_content": [{"type": "image", "content": "/x.png"}]})
    assert r.status_code == 200, r.text
    assert r.json()["assistant_message"]["content"] == "multimodal answer"
    assert r.json()["conversation"]["retrieval_mode"] == "multimodal"


# ---------------- 向后兼容 ----------------
def test_query_backward_compatible(client):
    r = client.post("/query", json={"query": "旧接口", "mode": "mix", "top_k": 20})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"].startswith("answer for")
    assert body["sources"][0]["type"] == "table"
    assert body["retrieval"]["num_hits"] == 2


def test_query_multimodal_backward_compatible(client):
    r = client.post("/query/multimodal",
                    json={"query": "图", "content": [{"type": "image", "content": "/a.png"}]})
    assert r.status_code == 200
    assert r.json()["answer"] == "multimodal answer"


# ---------------- Storage 单元测试 ----------------
def test_storage_empty(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    assert s.list_conversations() == []


def test_storage_create_read(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="T", model_id="m1")
    got = s.get_conversation(c.id)
    assert got.title == "T" and got.model_id == "m1"


def test_storage_update(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="A")
    s.update_conversation(c.id, {"title": "B", "retrieval_mode": "hybrid"})
    got = s.get_conversation(c.id)
    assert got.title == "B" and got.retrieval_mode == "hybrid"


def test_storage_delete(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="X")
    assert s.delete_conversation(c.id) is True
    with pytest.raises(ConversationNotFoundError):
        s.get_conversation(c.id)
    with pytest.raises(ConversationNotFoundError):
        s.delete_conversation(c.id)  # 删除不存在 -> 抛错，不静默


def test_storage_json_persistence(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="持久化")
    # 重启模拟：新建一个指向同一目录的 Store
    s2 = ConversationStore(str(tmp_path / "s"))
    got = s2.get_conversation(c.id)
    assert got.title == "持久化"
    assert (tmp_path / "s" / "conversations.json").exists()


def test_storage_messages_persist_across_restart(tmp_path):
    from querynest.core.models import Message
    from querynest.storage.conversation_store import new_id

    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="消息持久化")
    s.add_message(c.id, Message(id=new_id("m"), conversation_id=c.id,
                                role="user", content="你好"))
    s.add_message(c.id, Message(id=new_id("m"), conversation_id=c.id,
                                role="assistant", content="回复",
                                sources=[{"document_name": "a.pdf"}],
                                trace_id="tr-1"))
    s2 = ConversationStore(str(tmp_path / "s"))
    msgs = s2.get_messages(c.id)
    assert len(msgs) == 2
    assert msgs[1].sources[0]["document_name"] == "a.pdf"
    assert msgs[1].trace_id == "tr-1"
    assert s2.get_conversation(c.id).message_count == 2


def test_storage_missing_messages_file(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="空消息")
    assert s.get_messages(c.id) == []  # 缺失消息文件按空会话处理


def test_storage_corrupt_manifest(tmp_path):
    d = tmp_path / "s"
    d.mkdir(parents=True, exist_ok=True)
    (d / "conversations.json").write_text("{ not valid json", encoding="utf-8")
    s = ConversationStore(str(d))
    assert s.list_conversations() == []  # 损坏清单按空仓库处理，不崩溃


def test_storage_corrupt_messages_file(tmp_path):
    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="损坏消息")
    (tmp_path / "s" / "messages" / f"{c.id}.json").write_text(
        "[ broken", encoding="utf-8")
    assert s.get_messages(c.id) == []  # 损坏消息文件按空会话处理


def test_storage_duplicate_message_rejected(tmp_path):
    from querynest.core.models import Message
    from querynest.storage.conversation_store import new_id

    s = ConversationStore(str(tmp_path / "s"))
    c = s.create_conversation(title="重复")
    m = Message(id="m-same", conversation_id=c.id, role="user", content="x")
    s.add_message(c.id, m)
    with pytest.raises(ValueError):
        s.add_message(c.id, Message(id="m-same", conversation_id=c.id,
                                    role="user", content="y"))


def test_storage_unknown_conversation_message(tmp_path):
    from querynest.core.models import Message
    from querynest.storage.conversation_store import new_id

    s = ConversationStore(str(tmp_path / "s"))
    with pytest.raises(ConversationNotFoundError):
        s.add_message("nope", Message(id=new_id("m"), conversation_id="nope",
                                      role="user", content="x"))
    with pytest.raises(ConversationNotFoundError):
        s.get_messages("nope")


def test_make_title_truncates_long():
    long_text = "请分析 multimodal_test_report.pdf 中的 Retrieval Benchmark 并且给出详细结论和建议"
    t = make_title(long_text)
    assert len(t) <= 48
    assert t.endswith("…")
    assert "\n" not in make_title("第一行\n第二行\n第三行")
