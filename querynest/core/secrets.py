"""QueryNest Secret 存储 —— 模型 API Key 的独立安全存储层。

设计原则（与审计要求一致）：
- Secret 绝不与模型 metadata（models.json）混存。
- 优先使用操作系统 Keyring（``keyring`` 可选依赖）；
  未安装 Keyring 时回退到运行目录下 ``secrets.json``（已被 .gitignore 忽略，不进入 Git）。
- 任何接口都不返回真实 Secret，前端只见掩码（由上层 ``ModelEntry.masked_api_key`` 处理）。

会话内 API Key 仍存留在内存的 ModelEntry 中，仅持久化写入此处（键值 = 模型 id）。
"""

from __future__ import annotations

import json
from pathlib import Path


class SecretStore:
    """按 ``model_id`` 存取 Secret。Keyring 不可用时落到忽略的 secrets.json。"""

    def __init__(self, base_dir: str = "", service: str = "QueryNest"):
        Base = Path(base_dir) if base_dir else Path(".")
        self._file = Base / "secrets.json"
        self._service = service
        self._kv: dict = {}
        self._load_file()
        self._keyring = None
        try:  # 可选依赖：未安装则降级到文件
            import keyring  # type: ignore

            self._keyring = keyring
        except Exception:  # noqa: BLE001
            self._keyring = None

    def _load_file(self) -> None:
        try:
            if self._file.exists():
                self._kv = json.loads(self._file.read_text(encoding="utf-8")) or {}
        except Exception:  # noqa: BLE001
            self._kv = {}

    def _save_file(self) -> None:
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(
                json.dumps(self._kv, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 —— 写入失败不致命，内存仍可用
            pass

    def set(self, model_id: str, value: str) -> None:
        if not value:
            return
        if self._keyring is not None:
            try:
                self._keyring.set_password(self._service, model_id, value)
                self._kv.pop(model_id, None)
                self._save_file()
                return
            except Exception:  # noqa: BLE001 —— Keyring 失败则回退文件
                pass
        self._kv[model_id] = value
        self._save_file()

    def get(self, model_id: str) -> str:
        if self._keyring is not None:
            try:
                v = self._keyring.get_password(self._service, model_id)
                if v:
                    return v
            except Exception:  # noqa: BLE001
                pass
        return self._kv.get(model_id) or ""

    def delete(self, model_id: str) -> None:
        self._kv.pop(model_id, None)
        self._save_file()
        if self._keyring is not None:
            try:
                self._keyring.delete_password(self._service, model_id)
            except Exception:  # noqa: BLE001
                pass