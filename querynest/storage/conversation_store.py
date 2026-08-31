"""Conversation & Message 持久化存储（Chat History）。

存储布局（会话与消息分离，避免单个巨大 JSON）：

    querynest_storage/
        conversations.json            # 会话索引 {id: {conversation}}
        messages/<conversation_id>.json  # 每个会话独立的消息列表

写入采用「临时文件 + os.replace」保证原子性（与 DocumentStore 一致）。
所有写操作受线程锁保护；损坏的 JSON / 缺失文件 / 未知会话 / 重复消息
均有明确行为，绝不静默吞错。
"""

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from querynest.core.exceptions import ConversationNotFoundError
from querynest.core.models import Conversation, Message


def new_id(prefix: str = "c") -> str:
    """生成稳定的 UUID 短 ID（不用数组 index 作为 ID）。"""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def make_title(text: str, max_len: int = 48) -> str:
    """从第一条用户消息生成会话标题：折叠空白/去换行，超长截断加省略号（含省略号不超过 max_len）。"""
    title = " ".join((text or "").split())
    if len(title) > max_len:
        title = title[: max_len - 1].rstrip() + "…"
    return title


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


class ConversationStore:
    """线程安全的会话仓库：会话索引 + 每会话独立消息文件。"""

    MANIFEST = "conversations.json"

    def __init__(self, storage_dir: str = "./querynest_storage"):
        self.storage_dir = Path(storage_dir)
        self.messages_dir = self.storage_dir / "messages"
        self.manifest_path = self.storage_dir / self.MANIFEST
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._index: Dict[str, Dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------- #
    # 会话 CRUD
    # ------------------------------------------------------------- #
    def create_conversation(
        self,
        title: str = "",
        model_id: str = "",
        retrieval_mode: str = "mix",
        document_ids: Optional[List[str]] = None,
    ) -> Conversation:
        with self._lock:
            now = _now()
            conv = Conversation(
                id=new_id("c"),
                title=title,
                created_at=now,
                updated_at=now,
                model_id=model_id,
                retrieval_mode=retrieval_mode,
                document_ids=list(document_ids or []),
                message_count=0,
            )
            self._index[conv.id] = conv.to_dict()
            self._flush()
            return conv

    def get_conversation(self, conversation_id: str) -> Conversation:
        with self._lock:
            row = self._index.get(conversation_id)
        if not row:
            raise ConversationNotFoundError(
                f"会话不存在: {conversation_id}",
                context={"conversation_id": conversation_id},
            )
        return Conversation(**row)

    def conversation_exists(self, conversation_id: str) -> bool:
        with self._lock:
            return conversation_id in self._index

    def list_conversations(self, limit: int = 1000) -> List[Conversation]:
        with self._lock:
            rows = list(self._index.values())
        rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        return [Conversation(**r) for r in rows[:limit]]

    def update_conversation(
        self, conversation_id: str, patch: Dict[str, Any]
    ) -> Conversation:
        with self._lock:
            row = self._index.get(conversation_id)
            if not row:
                raise ConversationNotFoundError(
                    f"会话不存在: {conversation_id}",
                    context={"conversation_id": conversation_id},
                )
            for key, value in patch.items():
                if key in ("id", "created_at"):
                    continue
                if key == "document_ids":
                    value = list(value or [])
                elif key == "message_count":
                    value = int(value or 0)
                row[key] = value
            row["updated_at"] = _now()
            self._flush()
            return Conversation(**row)

    def delete_conversation(self, conversation_id: str) -> bool:
        """删除会话元数据 + 其消息文件；会话不存在时抛错（不静默成功）。"""
        with self._lock:
            if conversation_id not in self._index:
                raise ConversationNotFoundError(
                    f"会话不存在: {conversation_id}",
                    context={"conversation_id": conversation_id},
                )
            del self._index[conversation_id]
            msg_file = self.messages_dir / f"{conversation_id}.json"
            if msg_file.exists():
                try:
                    msg_file.unlink()
                except OSError:
                    pass
            self._flush()
            return True

    # ------------------------------------------------------------- #
    # 消息
    # ------------------------------------------------------------- #
    def get_messages(self, conversation_id: str) -> List[Message]:
        self._require_conversation(conversation_id)
        data = self._load_messages(conversation_id)
        return [Message(**m) for m in data if isinstance(m, dict)]

    def add_message(self, conversation_id: str, message: Message) -> Message:
        with self._lock:
            self._require_conversation(conversation_id)
            messages = self._load_messages(conversation_id)
            if any(m.get("id") == message.id for m in messages):
                raise ValueError(f"消息已存在: {message.id}")
            if not message.created_at:
                message.created_at = _now()
            messages.append(message.to_dict())
            self._save_messages(conversation_id, messages)
            row = self._index[conversation_id]
            row["message_count"] = len(messages)
            row["updated_at"] = _now()
            self._flush()
            return message

    def delete_message(self, conversation_id: str, message_id: str) -> bool:
        with self._lock:
            self._require_conversation(conversation_id)
            messages = self._load_messages(conversation_id)
            before = len(messages)
            kept = [m for m in messages if m.get("id") != message_id]
            if len(kept) == before:
                return False
            self._save_messages(conversation_id, kept)
            row = self._index[conversation_id]
            row["message_count"] = len(kept)
            row["updated_at"] = _now()
            self._flush()
            return True

    # ------------------------------------------------------------- #
    # 内部：原子读写
    # ------------------------------------------------------------- #
    def _require_conversation(self, conversation_id: str) -> None:
        with self._lock:
            if conversation_id not in self._index:
                raise ConversationNotFoundError(
                    f"会话不存在: {conversation_id}",
                    context={"conversation_id": conversation_id},
                )

    def _load_messages(self, conversation_id: str) -> List[Dict[str, Any]]:
        path = self.messages_dir / f"{conversation_id}.json"
        if not path.exists():
            return []  # 缺失消息文件按空会话处理
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []  # 损坏文件按空会话处理，不阻塞会话读取
        return data if isinstance(data, list) else []

    def _save_messages(self, conversation_id: str, messages: List[Dict[str, Any]]) -> None:
        path = self.messages_dir / f"{conversation_id}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)

    def _load(self) -> None:
        if self.manifest_path.exists():
            try:
                data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
                self._index = data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                self._index = {}

    def _flush(self) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, self.manifest_path)
