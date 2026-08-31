"""
QueryNest API 服务（FastAPI）

统一 API 入口：

- ``POST /documents``            上传/入库文档
- ``GET  /documents``            列出现有文档
- ``GET  /documents/{id}``       获取单个文档
- ``DELETE /documents/{id}``     删除文档
- ``POST /query``                问答
- ``POST /query/multimodal``     多模态问答
- ``GET  /health``                健康检查

引擎 / 依赖（fastapi、lightrag）未安装时，``create_app`` 仍可安全返回服务，
但涉及引擎的端点会返回明确的 503 错误，方便渐进式部署。

依赖 FastAPI；如未安装，调用 ``serve`` / ``create_app`` 会给出清晰提示。
"""

import asyncio
import atexit
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from querynest.core.exceptions import (
    ConversationNotFoundError,
    DocumentNotFoundError,
    QueryNestError,
    RetrievalError,
)
from querynest.core.models import Conversation, Message, RetrievalResult
from querynest.storage.conversation_store import (
    ConversationStore,
    make_title,
    new_id,
)
try:  # pragma: no cover - depends on fastapi
    from fastapi import FastAPI, HTTPException, Query as FQuery, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from pydantic import BaseModel, Field
    _FASTAPI_OK = True
except Exception:  # noqa: BLE001
    _FASTAPI_OK = False


def _require_fastapi():
    if not _FASTAPI_OK:
        raise QueryNestError(
            "启动 QueryNest API 需要 fastapi 与 uvicorn："
            "`pip install fastapi uvicorn`"
        )


# -------------------------------------------------------------------- #
# Pydantic 请求/响应模型
# -------------------------------------------------------------------- #
if _FASTAPI_OK:  # pragma: no cover - depends on fastapi

    class SourceOut(BaseModel):
        document: str = ""
        page: int = 0
        type: str = ""
        score: float = 0.0
        document_id: str = ""
        source: str = ""
        text: str = ""

    class QueryResponse(BaseModel):
        answer: str = ""
        sources: List[SourceOut] = Field(default_factory=list)
        retrieval: Dict[str, Any] = Field(default_factory=dict)
        metadata: Dict[str, Any] = Field(default_factory=dict)

    class QueryRequest(BaseModel):
        query: str
        mode: str = "mix"
        top_k: int = 20
        history: Optional[List[Dict[str, str]]] = None
        system_prompt: Optional[str] = None
        model_id: Optional[str] = None  # 可选：指定本次问答使用的聊天模型

    class MultimodalContent(BaseModel):
        type: str = "image"
        content: str = ""       # 路径或 base64
        # 兼容底层引擎的字段命名（不同 content 类型常使用自己的字段）
        img_path: str = ""
        table_data: str = ""
        latex: str = ""

    class MultimodalQueryRequest(BaseModel):
        query: str
        content: List[MultimodalContent] = Field(default_factory=list)
        mode: str = "mix"
        system_prompt: Optional[str] = None
        model_id: Optional[str] = None  # 可选：指定本次多模态问答使用的模型

    class ConversationCreate(BaseModel):
        title: str = ""
        model_id: Optional[str] = None
        retrieval_mode: str = "mix"
        document_ids: List[str] = Field(default_factory=list)

    class ConversationUpdate(BaseModel):
        title: Optional[str] = None
        model_id: Optional[str] = None
        retrieval_mode: Optional[str] = None
        document_ids: Optional[List[str]] = None

    class MessageCreate(BaseModel):
        content: str
        model_id: Optional[str] = None
        mode: str = "mix"          # mix / multimodal
        top_k: int = 20
        multimodal_content: List[Dict[str, Any]] = Field(default_factory=list)


# -------------------------------------------------------------------- #
# 应用工厂
# -------------------------------------------------------------------- #
# 前端已迁移至独立文件：querynest/api/static/index.html


PROJ_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _load_app_html() -> str:
    """加载独立前端页面（static/index.html）。"""
    index = _STATIC_DIR / "index.html"
    if index.exists():
        return index.read_text(encoding="utf-8")
    return (
        "<html><body><h1>UI resource missing</h1>"
        "<p>querynest/api/static/index.html 未找到，请确认前端文件存在。</p></body></html>"
    )


def _find_soffice() -> Optional[str]:
    """查找 LibreOffice 可执行文件（用于旧版 .doc 自动转档）。

    优先级：PATH 中的 ``soffice``/``libreoffice`` → 常见安装目录 glob。
    """
    try:
        from shutil import which
    except Exception:  # noqa: BLE001
        which = None  # type: ignore[assignment]
    if which:
        s = which("soffice") or which("libreoffice")
        if s:
            return s
    import glob

    for pattern in (
        "/c/Program Files/LibreOffice*/program/soffice.exe",
        "/c/Program Files (x86)/LibreOffice*/program/soffice.exe",
        "C:/Program Files/LibreOffice/program/soffice.exe",
        "C:/Program Files (x86)/LibreOffice/program/soffice.exe",
    ):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


def _convert_via_word(src: Path) -> Path:
    """用 Microsoft Word COM 把旧版 .doc 转成 .docx（仅 Windows + 装有 Office）。

    返回转换后的 .docx 路径；任何异常都向上抛出，由调用方回退到 LibreOffice。

    注意：服务在 uvicorn worker 线程中调用本函数，而 COM 要求每个线程先
    ``CoInitializeEx``；``win32.Dispatch`` 只在主线程自动初始化，因此在子线程
    （如 API 请求处理线程）中必须显式初始化/反初始化，否则会静默失败并错误回退到
    LibreOffice。
    """
    try:
        import pythoncom
        import win32com.client as win32
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"pywin32 不可用：{e}")
    # 在当前线程初始化 COM（服务在子线程调用时不会自动初始化）
    com_initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        com_initialized = True
    except Exception:  # noqa: BLE001 - 线程已初始化则忽略
        com_initialized = False
    out_dir = src.parent
    target = out_dir / (src.stem + "_converted.docx")
    if target.exists():
        try:
            target.unlink()
        except OSError:  # noqa: BLE001
            pass
    # Word 按扩展名判断文件格式：若文件内容是旧版 .doc 二进制，但扩展名被
    # 改成了 .docx（常见于“只改后缀”的假 .docx），Word 会拒绝打开并报
    # “格式与扩展名不匹配”。因此复制为临时 .doc 再打开。
    src_to_open = src
    tmp_doc_path = None
    if src.suffix.lower() == ".docx" and _is_ole(src):
        import shutil
        import tempfile

        fd, tmp_doc_path = tempfile.mkstemp(suffix=".doc", prefix="qn_conv_")
        os.close(fd)
        shutil.copy2(src, tmp_doc_path)
        src_to_open = Path(tmp_doc_path)
    word = None
    doc = None
    try:
        word = win32.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(src_to_open))
        # 16 = wdFormatXMLDocument（标准 .docx）
        doc.SaveAs2(str(target), FileFormat=16)
    finally:
        try:
            if doc is not None:
                doc.Close(SaveChanges=0)
        except Exception:  # noqa: BLE001
            pass
        try:
            if word is not None:
                word.Quit()
        except Exception:  # noqa: BLE001
            pass
        if com_initialized:
            try:
                pythoncom.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass
        if tmp_doc_path and os.path.exists(tmp_doc_path):
            try:
                os.remove(tmp_doc_path)
            except OSError:  # noqa: BLE001
                pass
    if not target.exists():
        raise RuntimeError("Word 转换未生成目标文件")
    return target


def _convert_doc_to_docx(src: Path) -> Path:
    """把旧版 .doc 自动转成 .docx，以兼容旧格式上传。

    优先 Microsoft Word（本机装有 Office 时最可靠），回退 LibreOffice。
    两者都不可用则给出明确中文指引。
    """
    # 1) 优先 Word COM
    try:
        return _convert_via_word(src)
    except Exception as _word_err:  # noqa: BLE001 - 回退到 LibreOffice
        import sys

        print(
            f"[QueryNest] Word COM 转换失败，回退 LibreOffice：{type(_word_err).__name__}: {_word_err}",
            file=sys.stderr,
        )
        pass
    # 2) 回退 LibreOffice 无头模式
    soffice = _find_soffice()
    if not soffice:
        raise QueryNestError(
            "未检测到可用的 Word 或 LibreOffice，无法直接解析旧版 .doc 文件。"
            "请安装 Microsoft Office 或 LibreOffice，"
            "或在 Word/WPS 中将文件「另存为 .docx」后重新上传。"
        )
    out_dir = src.parent
    target = out_dir / (src.stem + ".docx")
    if target.exists():
        try:
            target.unlink()
        except OSError:  # noqa: BLE001
            pass
    # 指定独立的无头配置目录，避免与可能运行的桌面版 LibreOffice 实例冲突
    # （否则会出现 "soffice.bin already running / source file could not be loaded"）。
    import tempfile

    lo_profile = tempfile.mkdtemp(prefix="qn_libreoffice_")
    user_install = "file:///" + lo_profile.replace("\\", "/")
    cmd = [
        soffice,
        f"-env:UserInstallation={user_install}",
        "--headless",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_dir),
        str(src),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired:  # noqa: BLE001
        raise QueryNestError(f"LibreOffice 转换超时（180s）：{src.name}")
    if result.returncode != 0 or not target.exists():
        detail = (result.stderr or result.stdout or "").strip()
        raise QueryNestError(
            f"自动转换 .doc 失败（Word/LibreOffice 均不可用或报错：{detail or '未知错误'}）。"
            "请在 Word/WPS 中将文件「另存为 .docx」后重新上传。"
        )
    return target


def _is_ole(path: Path) -> bool:
    """判断文件是否为旧版 Word 二进制（OLE 复合文档，魔数 d0 cf 11 e0）。

    旧版 ``.doc`` 以及把 ``.doc`` 直接改后缀得到的“假 .docx”都是这种格式。
    """
    try:
        return path.read_bytes()[:4] == b"\xd0\xcf\x11\xe0"
    except Exception:  # noqa: BLE001
        return False


def _normalize_word_file(filename: Optional[str], path: Optional[str] = None) -> str:
    """兼容旧版 Word：无论扩展名是 ``.doc`` 还是“假 ``.docx``”。

    只要文件头是旧版 OLE 二进制，就自动用 Word/LibreOffice 转成真正的 ``.docx``；
    真正的 ``.docx``（ZIP 头 ``PK\\x03\\x04``）原样返回；其他类型文件直接放行。
    这样用户上传旧版文件无需手动“另存为”，做到真正兼容。
    """
    if not filename:
        return path or ""
    lower = filename.lower()
    if not (lower.endswith(".doc") or lower.endswith(".docx")):
        return path or ""
    if not path:
        raise QueryNestError(
            "未提供文件路径，无法转换旧版 Word 文件。请使用文件上传方式，"
            "或先安装 Word/LibreOffice。"
        )
    src = Path(path)
    if _is_ole(src):
        # 旧版二进制：不论是 .doc 还是仅改后缀的 .docx，都自动转档
        converted = _convert_doc_to_docx(src)
        return str(converted)
    # 非 OLE：假定已是真正的 .docx（ZIP 头），交由解析器处理
    return str(src)


_ARCHIVE_SUFFIXES = (".zip", ".tar", ".tar.gz", ".tgz")


def _is_archive(filename: Optional[str]) -> bool:
    """判断文件名是否为受支持的压缩包（zip / tar / tar.gz / tgz）。"""
    if not filename:
        return False
    lower = filename.lower()
    return lower.endswith(_ARCHIVE_SUFFIXES)


# 压缩包安全上限（解压前校验，避免压缩炸弹 / 海量条目拖垮服务）
_ARCHIVE_MAX_ENTRIES = 2000        # 压缩包内条目数上限
_ARCHIVE_MAX_ENTRY_BYTES = 512 * 1024 * 1024   # 单文件解压后上限：512 MiB
_ARCHIVE_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 累计解压总量上限：2 GiB


def _safe_extract(archive: Path, target_dir: Path) -> None:
    """安全解压 zip / tar 压缩包，防止路径穿越（zip-slip）逃逸到目标目录之外。

    同时做压缩炸弹防护：解压前按解压后体积累加校验，超限即拒绝，避免海量 / 超高
    压缩比内容把磁盘写满。仅支持标准库即可覆盖的格式；``.rar`` / ``.7z`` 需要额
    外依赖，暂不支持。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target_resolved = target_dir.resolve()
    name = archive.name.lower()
    if name.endswith(".zip"):
        import zipfile

        with zipfile.ZipFile(archive) as zf:
            if len(zf.infolist()) > _ARCHIVE_MAX_ENTRIES:
                raise QueryNestError(
                    f"压缩包条目数超过上限（{_ARCHIVE_MAX_ENTRIES}），已拒绝"
                )
            total_size = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # 路径穿越防护（含 ``../`` 与绝对路径两种形式）
                if not (target_resolved / info.filename).resolve().is_relative_to(
                    target_resolved
                ):
                    raise QueryNestError(
                        f"压缩包内存在越界路径，已拒绝：{info.filename}"
                    )
                entry_size = info.file_size
                total_size += entry_size
                if entry_size > _ARCHIVE_MAX_ENTRY_BYTES:
                    raise QueryNestError(
                        f"压缩包内单个文件过大（{entry_size} 字节），已拒绝：{info.filename}"
                    )
                if total_size > _ARCHIVE_MAX_TOTAL_BYTES:
                    raise QueryNestError(
                        "压缩包累计解压体积超过上限（2 GiB），已拒绝解压"
                    )
            zf.extractall(target_dir)
    else:  # tar / tar.gz / tgz
        import tarfile

        with tarfile.open(archive, "r:*") as tf:
            members = tf.getmembers()
            if len(members) > _ARCHIVE_MAX_ENTRIES:
                raise QueryNestError(
                    f"压缩包条目数超过上限（{_ARCHIVE_MAX_ENTRIES}），已拒绝"
                )
            total_size = 0
            for member in members:
                if member.isdir():
                    continue
                if not (target_resolved / member.name).resolve().is_relative_to(
                    target_resolved
                ):
                    raise QueryNestError(
                        f"压缩包内存在越界路径，已拒绝：{member.name}"
                    )
                total_size += member.size
                if member.size > _ARCHIVE_MAX_ENTRY_BYTES:
                    raise QueryNestError(
                        f"压缩包内单个文件过大（{member.size} 字节），已拒绝：{member.name}"
                    )
                if total_size > _ARCHIVE_MAX_TOTAL_BYTES:
                    raise QueryNestError(
                        "压缩包累计解压体积超过上限（2 GiB），已拒绝解压"
                    )
            tf.extractall(target_dir, filter="data")  # Python 3.12+


# -------------------------------------------------------------------- #
# 异步后台入库队列
# -------------------------------------------------------------------- #
# 大文档（尤其是扫描图）解析很吃 CPU，若在前端请求内同步解析，会把服务拖到
# 失去响应，导致前端所有请求超时报 "Failed to fetch"。这里改为：
#   提交接口立即返回 task_id，文件在后台单 worker 串行解析，
#   前端轮询 /documents/tasks/{task_id} 获取进度，服务始终可响应。
_INGEST_TASKS: Dict[str, dict] = {}
_INGEST_QUEUE: Optional["asyncio.Queue[str]"] = None
_INGEST_WORKER_RUNNING = False
_INGEST_ENGINE: Dict[str, Any] = {"engine": None}
_INGEST_KEEP_TASKS = 200


async def _ingest_worker_loop() -> None:
    """后台单 worker：逐个文件串行入库，避免并发写索引与 CPU 打满。"""
    while True:
        task_id = await _INGEST_QUEUE.get()  # type: ignore[union-attr]
        task = _INGEST_TASKS.get(task_id)
        if task is None:
            continue
        engine = _INGEST_ENGINE.get("engine")
        task["status"] = "processing"
        for item in task["files"]:
            try:
                if engine is None:
                    raise RuntimeError("引擎未就绪")
                meta = await engine.ingest(
                    item["path"], parse_method=item.get("parse_method")
                )
                task["ingested"].append(
                    {"document_id": meta.document_id, "filename": item["filename"]}
                )
            except Exception as e:  # noqa: BLE001
                task["failed"].append(
                    {"filename": item["filename"], "error": str(e)}
                )
            task["done"] += 1
        task["status"] = "done"
        task["finished_at"] = time.time()
        _prune_ingest_tasks()


def _prune_ingest_tasks() -> None:
    """任务保留上限：超限时清理最旧的已完成任务，避免内存无限增长。"""
    if len(_INGEST_TASKS) <= _INGEST_KEEP_TASKS:
        return
    done_ids = sorted(
        (tid for tid, t in _INGEST_TASKS.items() if t.get("status") == "done"),
        key=lambda tid: _INGEST_TASKS[tid].get("finished_at", 0),
    )
    import time

    for tid in done_ids:
        if len(_INGEST_TASKS) <= _INGEST_KEEP_TASKS:
            break
        _INGEST_TASKS.pop(tid, None)


def _ensure_ingest_worker() -> None:
    """惰性启动后台 worker（必须在 asyncio 运行循环内调用）。"""
    global _INGEST_QUEUE, _INGEST_WORKER_RUNNING
    loop = asyncio.get_running_loop()
    if _INGEST_QUEUE is None:
        _INGEST_QUEUE = asyncio.Queue()
    if not _INGEST_WORKER_RUNNING:
        _INGEST_WORKER_RUNNING = True
        loop.create_task(_ingest_worker_loop())


def create_app(engine: Any = None, conversation_store: Any = None) -> "FastAPI":
    """创建 FastAPI 应用。

    Args:
        engine: 可选的特性注入（提供 ``ingest/query/query_multimodal/
            list_documents/get_document/delete_document``）。默认延迟构建。
        conversation_store: 可选的会话仓库注入（测试用）。默认按配置延迟构建。
    """
    _require_fastapi()

    app = FastAPI(
        title="QueryNest - Multimodal Document Intelligence & RAG",
        version="2.0.0",
        description="面向复杂文档的多模态 Retrieval-Augmented Generation 系统",
    )
    app.state.engine = engine
    app.state.registry = None
    app.state.conversation_store = conversation_store
    from querynest.core.model_registry import ModelRegistry, RegistryError, KIND_LABEL

    def _get_registry() -> ModelRegistry:
        if app.state.registry is None:
            from querynest import QueryNestConfig as _QNConfig

            _cfg = _QNConfig()
            app.state.registry = ModelRegistry(_cfg.working_dir)
        return app.state.registry

    def _get_conversation_store() -> ConversationStore:
        if app.state.conversation_store is None:
            from querynest import QueryNestConfig as _QNConfig

            _cfg = _QNConfig()
            app.state.conversation_store = ConversationStore(_cfg.storage_dir)
        return app.state.conversation_store

    def _get_engine():
        eng = app.state.engine
        if eng is None:
            eng = _build_default_engine(registry=_get_registry())
            app.state.engine = eng
        return eng

    def _active_map(reg: ModelRegistry):
        """当前各类用途生效的模型（用于前端模型选择器 / 模型中心高亮）。"""
        out = {}
        for kind in ("chat", "vision", "embedding", "reranker"):
            try:
                out[kind] = reg.resolve(kind).to_dict()
            except RegistryError:
                out[kind] = None
        return out

    def _validate_query_model(model_id, kind: str = "chat"):
        """API 层先行校验本次请求显式指定的模型，任何问题返回明确 4xx（不落到 500）。"""
        if not model_id:
            return
        reg = _get_registry()
        try:
            m = reg.get(model_id)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        if m.kind != kind:
            raise HTTPException(
                status_code=422,
                detail={"error": f"模型 {model_id} 用途为「{m.kind}」，本次问答需要 {kind} 模型。"},
            )
        if not m.enabled:
            raise HTTPException(
                status_code=422,
                detail={"error": "该模型已禁用，无法用于问答。请先在模型中心启用或切换到其他模型。"},
            )
        from querynest.core.providers import get_provider_adapter

        adapter = get_provider_adapter(m.provider, m)
        if adapter.requires_api_key and not m.api_key:
            raise HTTPException(
                status_code=422,
                detail={"error": "API key is not configured：该模型未配置 API Key，无法用于问答。"},
            )

    # ---------------- health ----------------
    @app.get("/", include_in_schema=False)
    def chat_home():
        # no-cache：前端为内嵌单文件，需保证开发/更新后刷新即生效，避免浏览器缓存旧页面
        return HTMLResponse(
            _load_app_html(),
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )

    @app.get("/health")
    def health():
        from querynest import __version__

        eng = getattr(app.state, "engine", None)
        return {
            "status": "ok",
            "service": "querynest",
            "version": __version__,
            "engine_ready": eng is not None,
        }

    # ---------------- models（模型中心，前后端联动） ----------------
    @app.get("/models")
    def models_list(kind: Optional[str] = None):
        reg = _get_registry()
        items = reg.list(kind)
        return {"models": [e.to_dict() for e in items], "active": _active_map(reg)}

    @app.post("/models")
    async def models_add(request: Request):
        body = await request.json()
        try:
            e = _get_registry().add(body, confirm=bool(body.get("confirm")))
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"model": e.to_dict()}

    @app.put("/models/{mid}")
    async def models_update(mid: str, request: Request):
        body = await request.json()
        try:
            e = _get_registry().update(mid, body, confirm=bool(body.get("confirm")))
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"model": e.to_dict()}

    @app.delete("/models/{mid}")
    def models_delete(mid: str):
        try:
            _get_registry().delete(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"deleted": mid}

    @app.post("/models/{mid}/activate")
    def models_activate(mid: str):
        reg = _get_registry()
        try:
            e = reg.activate(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        # 把 chat/vision 的激活结果立即热替换到引擎；embedding 走维度护栏 + reindex 流程
        applied = []
        try:
            applied = _get_engine().apply_active_models(reg, kinds=("chat", "vision"))
        except RuntimeError:
            pass  # 引擎尚未初始化时（首次激活）跳过热替换，下次构造自然读取注册表
        return {"model": e.to_dict(), "applied_kinds": applied}

    @app.post("/models/{mid}/default")
    def models_set_default(mid: str):
        reg = _get_registry()
        try:
            reg.set_default(mid)
            e = reg.get(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"model": e.to_dict()}

    @app.post("/models/{mid}/enable")
    def models_enable(mid: str):
        reg = _get_registry()
        try:
            e = reg.enable(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"model": e.to_dict()}

    @app.post("/models/{mid}/disable")
    def models_disable(mid: str):
        reg = _get_registry()
        try:
            e = reg.disable(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        return {"model": e.to_dict()}

    @app.post("/models/{mid}/test")
    async def models_test(mid: str):
        reg = _get_registry()
        try:
            e = reg.get(mid)
        except RegistryError as ex:
            raise HTTPException(status_code=422, detail={"error": str(ex)})
        # 真实测试：Registry → Provider Adapter → Provider API（针对 kind 选择适配器）
        from querynest.core.providers import get_provider_adapter

        adapter = get_provider_adapter(e.provider, e)
        result = adapter.test_connection(e)
        # 无 API Key 属于客户端可修正的错误，返回明确 4xx（不是 500）
        if not result.ok and result.category == "not_configured":
            raise HTTPException(
                status_code=422,
                detail={"error": result.message, "category": result.category,
                        "model": e.model, "provider": e.provider},
            )
        return result.to_dict()

    # ---------------- documents ----------------
    @app.get("/documents")
    def documents_list():
        return {"documents": _get_engine().list_documents()}

    @app.get("/documents/{document_id}")
    def documents_get(document_id: str):
        try:
            return _get_engine().get_document(document_id)
        except DocumentNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())

    @app.post("/documents")
    async def documents_upload(request: Request):
        body = await request.json()
        path = body.get("path") or body.get("file_path") or body.get("file")
        parse_method = body.get("parse_method")
        doc_id = body.get("document_id")
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path/file_path 字段")
        final_path = _normalize_word_file(Path(path).name, str(path))
        try:
            meta = await _get_engine().ingest(final_path, parse_method=parse_method, doc_id=doc_id)
            return {"document_id": meta.document_id, "document": meta.to_dict()}
        except QueryNestError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        except ValueError as e:  # noqa: BLE001 - 空文档/无内容可提取等解析失败
            raise HTTPException(
                status_code=422,
                detail={"error": "document_parse_failed", "message": str(e)},
            )

    @app.post("/documents/upload")
    async def documents_upload_file(request: Request):
        """直接上传文件（浏览器 FileReader 转 base64 后提交此接口）。"""
        import base64
        import traceback
        from pathlib import Path

        body = await request.json()
        filename = body.get("filename") or ""
        content_b64 = body.get("content") or ""
        parse_method = body.get("parse_method")
        if not filename or not content_b64:
            raise HTTPException(status_code=400, detail="缺少 filename/content")
        safe = Path(filename).name  # 取纯文件名，避免路径穿越
        try:
            data = base64.b64decode(content_b64)
        except Exception:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="content 不是有效的 base64")
        from querynest import QueryNestConfig

        cfg = QueryNestConfig()
        upload_dir = Path(cfg.parser_output_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        dest = upload_dir / safe
        dest.write_bytes(data)
        if _is_archive(filename):
            # 压缩包：安全解压后递归入库内部所有受支持的文件
            extract_dir = upload_dir / (Path(safe).stem + "_unpacked")
            try:
                _safe_extract(dest, extract_dir)
                supported = {
                    (e if e.startswith(".") else "." + e).lower()
                    for e in cfg.supported_file_extensions
                }
                files = sorted(
                    (
                        p
                        for p in extract_dir.rglob("*")
                        if p.is_file()
                        and not _is_archive(p.name)
                        and p.suffix.lower() in supported
                    ),
                    key=lambda p: str(p).lower(),
                )
                ingested, failed = [], []
                for f in files:
                    try:
                        final = _normalize_word_file(f.name, str(f))
                        meta = await _get_engine().ingest(
                            final, parse_method=parse_method
                        )
                        ingested.append(
                            {"document_id": meta.document_id, "filename": f.name}
                        )
                    except Exception as e:  # noqa: BLE001
                        failed.append({"filename": f.name, "error": str(e)})
                return {
                    "extracted": "ok",
                    "ingested_count": len(ingested),
                    "ingested": ingested,
                    "failed": failed,
                }
            except QueryNestError as e:
                raise HTTPException(status_code=422, detail=e.to_dict())
            except Exception as e:  # noqa: BLE001
                raise HTTPException(
                    status_code=422,
                    detail={"error": "archive_extract_failed", "message": str(e)},
                )
            finally:
                shutil.rmtree(extract_dir, ignore_errors=True)
        final_path = _normalize_word_file(filename, str(dest.resolve()))
        try:
            meta = await _get_engine().ingest(final_path, parse_method=parse_method)
        except QueryNestError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        except ValueError as e:  # noqa: BLE001
            raise HTTPException(
                status_code=422,
                detail={"error": "document_parse_failed", "message": str(e)},
            )
        except Exception as e:  # noqa: BLE001
            # 兜底：防止未捕获异常变成纯文本 500，导致前端 JSON.parse 失败。
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "internal_server_error",
                    "message": str(e),
                    "traceback": traceback.format_exc(),
                },
            )
        return {"document_id": meta.document_id, "document": meta.to_dict()}

    # ---------------- batch upload ----------------
    @app.post("/documents/upload/batch")
    async def documents_upload_batch(request: Request):
        """批量上传多个文件（浏览器 FileReader 转 base64 后提交此接口）。

        请求体：``{"files": [{"filename": "...", "content": "<base64>", "parse_method": "..."}]}``
        逐个入库；压缩包会解压后递归入库内部支持的文件。
        返回值：``{"batch": true, "total", "ingested_count", "failed_count", "ingested", "failed"}``
        """
        import base64
        import traceback
        from pathlib import Path

        body = await request.json()
        files = body.get("files") or []
        if not isinstance(files, list) or not files:
            raise HTTPException(
                status_code=400, detail="缺少 files 字段（应为非空数组）"
            )
        from querynest import QueryNestConfig

        cfg = QueryNestConfig()
        upload_dir = Path(cfg.parser_output_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        ingested, failed = [], []
        for item in files:
            filename = item.get("filename") or item.get("file") or ""
            content_b64 = item.get("content") or ""
            parse_method = item.get("parse_method")
            if not filename or not content_b64:
                failed.append(
                    {"filename": filename or "(未命名)", "error": "缺少 filename/content"}
                )
                continue
            safe = Path(filename).name  # 取纯文件名，避免路径穿越
            try:
                data = base64.b64decode(content_b64)
            except Exception:  # noqa: BLE001
                failed.append({"filename": safe, "error": "content 不是有效的 base64"})
                continue
            dest = upload_dir / safe
            try:
                dest.write_bytes(data)
            except Exception as e:  # noqa: BLE001
                failed.append({"filename": safe, "error": f"写入失败：{e}"})
                continue
            if _is_archive(filename):
                # 压缩包：安全解压后递归入库内部所有受支持的文件
                extract_dir = upload_dir / (Path(safe).stem + "_unpacked")
                try:
                    _safe_extract(dest, extract_dir)
                    supported = {
                        (e if e.startswith(".") else "." + e).lower()
                        for e in cfg.supported_file_extensions
                    }
                    files_in_archive = sorted(
                        (
                            p
                            for p in extract_dir.rglob("*")
                            if p.is_file()
                            and not _is_archive(p.name)
                            and p.suffix.lower() in supported
                        ),
                        key=lambda p: str(p).lower(),
                    )
                    for f in files_in_archive:
                        try:
                            final = _normalize_word_file(f.name, str(f))
                            meta = await _get_engine().ingest(
                                final, parse_method=parse_method
                            )
                            ingested.append(
                                {
                                    "document_id": meta.document_id,
                                    "filename": safe + "/" + f.name,
                                }
                            )
                        except Exception as e:  # noqa: BLE001
                            failed.append(
                                {"filename": safe + "/" + f.name, "error": str(e)}
                            )
                except Exception as e:  # noqa: BLE001
                    failed.append({"filename": safe, "error": f"解压失败：{e}"})
                finally:
                    shutil.rmtree(extract_dir, ignore_errors=True)
                continue
            final_path = _normalize_word_file(filename, str(dest.resolve()))
            try:
                meta = await _get_engine().ingest(final_path, parse_method=parse_method)
                ingested.append(
                    {"document_id": meta.document_id, "filename": safe}
                )
            except Exception as e:  # noqa: BLE001
                failed.append({"filename": safe, "error": str(e)})
        return {
            "batch": True,
            "total": len(files),
            "ingested_count": len(ingested),
            "failed_count": len(failed),
            "ingested": ingested,
            "failed": failed,
        }

    # ---------------- async queue ingest ----------------
    @app.post("/documents/ingest/batch-queue")
    async def documents_ingest_queue(request: Request):
        """接收批量文件（base64）后立即返回任务 ID，后台串行解析，避免拖死服务。

        请求体：``{"files": [{"filename","content","parse_method"?}]}``
        （压缩包会在提交阶段就地解压展开为多个文件项）。
        返回：``{"task_id","status":"pending","total"}``
        """
        import base64
        from pathlib import Path

        body = await request.json()
        entries = body.get("files") or []
        if not isinstance(entries, list) or not entries:
            raise HTTPException(status_code=400, detail="缺少 files 字段（应为非空数组）")
        engine = _get_engine()
        _INGEST_ENGINE["engine"] = engine
        from querynest import QueryNestConfig

        cfg = QueryNestConfig()
        upload_dir = Path(cfg.parser_output_dir) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        file_items = []
        for item in entries:
            filename = item.get("filename") or item.get("file") or ""
            content_b64 = item.get("content") or ""
            parse_method = item.get("parse_method")
            if not filename or not content_b64:
                continue
            safe = Path(filename).name  # 取纯文件名，避免路径穿越
            try:
                data = base64.b64decode(content_b64)
            except Exception:  # noqa: BLE001
                continue
            dest = upload_dir / safe
            try:
                dest.write_bytes(data)
            except Exception:  # noqa: BLE001
                continue
            if _is_archive(filename):
                # 压缩包：就地解压展开为多个受支持的内部文件项（沿用安全解压）
                extract_dir = upload_dir / (Path(safe).stem + "_unpacked")
                try:
                    _safe_extract(dest, extract_dir)
                except Exception as e:  # noqa: BLE001
                    file_items.append(
                        {"filename": safe, "path": str(dest.resolve()), "parse_method": parse_method, "error": f"解压失败：{e}"}
                    )
                    continue
                supported = {
                    (e if e.startswith(".") else "." + e).lower()
                    for e in cfg.supported_file_extensions
                }
                inner = sorted(
                    (
                        p
                        for p in extract_dir.rglob("*")
                        if p.is_file()
                        and not _is_archive(p.name)
                        and p.suffix.lower() in supported
                    ),
                    key=lambda p: str(p).lower(),
                )
                for f in inner:
                    file_items.append(
                        {
                            "filename": safe + "/" + f.name,
                            "path": str(f.resolve()),
                            "parse_method": parse_method,
                        }
                    )
                continue
            file_items.append(
                {"filename": safe, "path": str(dest.resolve()), "parse_method": parse_method}
            )
        if not file_items:
            raise HTTPException(status_code=400, detail="没有可入库的有效文件")
        task_id = uuid.uuid4().hex
        _INGEST_TASKS[task_id] = {
            "task_id": task_id,
            "status": "pending",
            "total": len(file_items),
            "done": 0,
            "ingested": [],
            "failed": [],
            "files": file_items,
            "created_at": time.time(),
            "finished_at": 0,
        }
        _ensure_ingest_worker()
        await _INGEST_QUEUE.put(task_id)  # type: ignore[union-attr]
        return {
            "task_id": task_id,
            "status": "pending",
            "total": len(file_items),
        }

    @app.get("/documents/tasks/{task_id}")
    def documents_task_status(task_id: str):
        """查询异步入库任务进度。返回 ingested/failed 汇总，供前端轮询。"""
        task = _INGEST_TASKS.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在或已被清理")
        return {
            "task_id": task["task_id"],
            "status": task["status"],
            "total": task["total"],
            "done": task["done"],
            "ingested": task["ingested"],
            "failed": task["failed"],
        }

    @app.delete("/documents/{document_id}")
    def documents_delete(document_id: str):
        ok = _get_engine().delete_document(document_id)
        if not ok:
            raise HTTPException(status_code=404, detail="文档不存在")
        # 文档元数据 + 缓存来源文本已删除；底层向量/图索引不在本层清理，
        # 明确标注，避免误以为已做完整删除造成数据不一致。
        return {
            "deleted": document_id,
            "index_cleanup": "pending",
            "metadata_deletion": "done",
        }

    # ---------------- conversations（会话与消息持久化） ----------------
    @app.get("/conversations")
    def conversations_list():
        store = _get_conversation_store()
        return {"conversations": [c.to_dict() for c in store.list_conversations()]}

    @app.post("/conversations")
    def conversations_create(req: ConversationCreate):
        store = _get_conversation_store()
        conv = store.create_conversation(
            title=req.title.strip(),
            model_id=req.model_id or "",
            retrieval_mode=req.retrieval_mode or "mix",
            document_ids=req.document_ids,
        )
        return {"conversation": conv.to_dict()}

    @app.get("/conversations/{conversation_id}")
    def conversations_get(conversation_id: str):
        store = _get_conversation_store()
        try:
            conv = store.get_conversation(conversation_id)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())
        return {"conversation": conv.to_dict()}

    @app.patch("/conversations/{conversation_id}")
    def conversations_update(conversation_id: str, req: ConversationUpdate):
        store = _get_conversation_store()
        patch: Dict[str, Any] = {}
        if req.title is not None:
            title = req.title.strip()
            if not title:
                raise HTTPException(status_code=422, detail="标题不能为空")
            if len(title) > 100:
                raise HTTPException(status_code=422, detail="标题过长（最多 100 字符）")
            patch["title"] = title
        if req.model_id is not None:
            patch["model_id"] = req.model_id
        if req.retrieval_mode is not None:
            patch["retrieval_mode"] = req.retrieval_mode
        if req.document_ids is not None:
            patch["document_ids"] = req.document_ids
        try:
            conv = store.update_conversation(conversation_id, patch)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())
        return {"conversation": conv.to_dict()}

    @app.delete("/conversations/{conversation_id}")
    def conversations_delete(conversation_id: str):
        store = _get_conversation_store()
        try:
            store.delete_conversation(conversation_id)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())
        return {"deleted": conversation_id}

    @app.get("/conversations/{conversation_id}/messages")
    def conversations_messages(conversation_id: str):
        store = _get_conversation_store()
        try:
            msgs = store.get_messages(conversation_id)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())
        return {"messages": [m.to_dict() for m in msgs]}

    @app.post("/conversations/{conversation_id}/messages")
    async def conversations_add_message(conversation_id: str, req: MessageCreate):
        store = _get_conversation_store()
        try:
            conv = store.get_conversation(conversation_id)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())

        content = (req.content or "").strip()
        if not content:
            raise HTTPException(status_code=422, detail="消息内容不能为空")
        effective_model = req.model_id or conv.model_id or None
        _validate_query_model(effective_model, "chat")

        # 0) 收集此前的多轮历史（用于问题改写 / 指代消解）；当前问题尚未入历史
        history_msgs = store.get_messages(conversation_id)
        history: List[Dict[str, str]] = []
        for m in history_msgs:
            if m.role == "user":
                history.append({"user": m.content, "assistant": ""})
            elif m.role == "assistant" and history:
                history[-1]["assistant"] = m.content

        # 1) 先保存 user 消息
        user_msg = Message(
            id=new_id("m"),
            conversation_id=conversation_id,
            role="user",
            content=content,
            model_id=effective_model or "",
        )
        store.add_message(conversation_id, user_msg)

        # 2) 第一条消息自动生成标题（不调用 LLM，避免额外成本/延迟/失败点）
        if not conv.title and conv.message_count <= 1:
            store.update_conversation(conversation_id, {"title": make_title(content)})

        # 3) 复用现有引擎执行真实 RAG（不重新实现检索）
        try:
            if req.mode == "multimodal":
                result = await _get_engine().query_multimodal(
                    content,
                    multimodal_content=req.multimodal_content,
                    mode=req.mode,
                    model_id=effective_model,
                )
            else:
                result = await _get_engine().query(
                    content,
                    mode=req.mode,
                    top_k=req.top_k,
                    model_id=effective_model,
                    history=history,
                )
        except RetrievalError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        except QueryNestError as e:
            raise HTTPException(status_code=500, detail=e.to_dict())

        # 4) 保存 assistant 消息：sources 直接来自真实 RetrievalResult，trace_id 来自真实 trace
        trace_id = (
            (result.metadata or {}).get("trace_id")
            or (result.retrieval or {}).get("trace_id")
            or ""
        )
        assistant_msg = Message(
            id=new_id("m"),
            conversation_id=conversation_id,
            role="assistant",
            content=result.answer,
            model_id=effective_model or "",
            sources=[c.to_dict() for c in result.sources],
            retrieval=result.retrieval,
            trace_id=trace_id,
        )
        store.add_message(conversation_id, assistant_msg)

        # 5) 会话绑定本次使用的模型与检索模式
        store.update_conversation(
            conversation_id,
            {
                "model_id": effective_model or conv.model_id,
                "retrieval_mode": req.mode,
            },
        )
        return {
            "user_message": user_msg.to_dict(),
            "assistant_message": assistant_msg.to_dict(),
            "conversation": store.get_conversation(conversation_id).to_dict(),
        }

    @app.delete("/conversations/{conversation_id}/messages/{message_id}")
    def conversations_delete_message(conversation_id: str, message_id: str):
        store = _get_conversation_store()
        try:
            ok = store.delete_message(conversation_id, message_id)
        except ConversationNotFoundError as e:
            raise HTTPException(status_code=404, detail=e.to_dict())
        if not ok:
            raise HTTPException(status_code=404, detail="消息不存在")
        return {"deleted": message_id}

    # ---------------- evaluation & settings（只读，供前端页面使用） ----------------
    @app.get("/api/evaluation")
    def api_evaluation():
        """读取评估指标结果（evaluation/results.json），只读、不改动任何索引。"""
        result_file = PROJ_ROOT / "evaluation" / "results.json"
        if not result_file.exists():
            return {
                "error": "未找到评估结果文件 evaluation/results.json（尚未运行评估）。",
                "metrics": {},
                "results": [],
            }
        try:
            return json.loads(result_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:  # noqa: BLE001
            return {"error": f"评估结果读取失败：{e}", "metrics": {}, "results": []}

    @app.post("/api/evaluation")
    async def api_evaluation_run(request: Request):
        """触发一次真实评估：用引擎的真实混合检索驱动 EvalRunner，
        逐题检索并计算 Recall / Precision / MRR / NDCG（Faithfulness 无判定器时
        如实标注，不硬编码 0），结果写入 evaluation/results.json 后返回。
        请求体（可选）：``{"dataset_path"?, "top_k"?}``，缺省用最小真实评测集。"""
        body: Dict[str, Any] = {}
        try:
            data = await request.json()
            if isinstance(data, dict):
                body = data
        except Exception:  # noqa: BLE001 - 无 body 或无有效 JSON 时按默认
            pass

        dataset = (
            str(body.get("dataset_path"))
            if body.get("dataset_path")
            else str(PROJ_ROOT / "evaluation" / "datasets" / "real_check.json")
        )
        top_k = int(body.get("top_k") or 10)

        engine = _get_engine()
        try:
            await engine._ensure_initialized()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"引擎未就绪：{e}")

        retriever_obj = getattr(engine, "_hybrid_retriever", None)
        if retriever_obj is None or not hasattr(retriever_obj, "retrieve_async"):
            raise HTTPException(
                status_code=503, detail="检索器未就绪，无法执行评估"
            )

        loop = asyncio.get_running_loop()

        def _retriever(question: str):
            # 检索器是 async，这里从后台 worker 线程调度到主事件循环同步等待
            future = asyncio.run_coroutine_threadsafe(
                retriever_obj.retrieve_async(question, top_k=top_k), loop
            )
            return future.result(timeout=180)

        from querynest.evaluation.runner import EvalRunner

        output_path = str(PROJ_ROOT / "evaluation" / "results.json")
        runner = EvalRunner(retriever=_retriever, output_path=output_path)
        try:
            report = await asyncio.to_thread(runner.run, dataset, top_k)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=500,
                detail=f"评估执行失败：{e}",
            )
        report["_executed_by"] = "api:POST/api/evaluation"
        report["_executed_real"] = True
        return report

    @app.get("/api/settings")
    def api_settings():
        """读取运行配置（不含明文密钥），只读。"""
        from querynest import QueryNestConfig

        cfg = QueryNestConfig()
        key = getattr(cfg, "llm_api_key", "") or ""
        masked = ("***" + key[-4:]) if key else ""
        return {
            "llm_model": getattr(cfg, "llm_model", ""),
            "llm_base_url": getattr(cfg, "llm_base_url", ""),
            "llm_temperature": getattr(cfg, "llm_temperature", 0.0),
            "embedding_model": getattr(cfg, "embedding_model", ""),
            "embedding_dim": getattr(cfg, "embedding_dim", 0),
            "vision_model": getattr(cfg, "vision_model", ""),
            "reranker_model": getattr(cfg, "reranker_model", ""),
            "enable_rerank": bool(getattr(cfg, "enable_rerank", False)),
            "parser": getattr(cfg, "parser", ""),
            "parse_method": getattr(cfg, "parse_method", ""),
            "working_dir": getattr(cfg, "working_dir", ""),
            "storage_dir": getattr(cfg, "storage_dir", ""),
            "parser_output_dir": getattr(cfg, "parser_output_dir", ""),
            "api_host": getattr(cfg, "api_host", ""),
            "api_port": getattr(cfg, "api_port", 0),
            "api_key_masked": masked,
        }

    # ---------------- trace（可观测性） ----------------
    @app.get("/api/traces")
    def api_traces(limit: int = FQuery(50, ge=1, le=200)):
        """列出最近查询 Trace（不含任何 Secret）。只读观测端点。"""
        from querynest.core.trace import trace_store

        return {"traces": trace_store.list(limit=limit)}

    @app.get("/api/traces/{trace_id}")
    def api_trace_detail(trace_id: str):
        """按 trace_id 获取单次查询的完整执行轨迹（步骤、耗时、状态）。"""
        from querynest.core.trace import trace_store

        trace = trace_store.get(trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail=f"Trace 不存在: {trace_id}")
        return trace.to_dict()

    # ---------------- query ----------------
    @app.post("/query", response_model=QueryResponse)
    async def query(req: QueryRequest):
        _validate_query_model(req.model_id, "chat")
        try:
            result = await _get_engine().query(
                req.query,
                mode=req.mode,
                top_k=req.top_k,
                history=req.history,
                system_prompt=req.system_prompt,
                model_id=req.model_id,
            )
        except RetrievalError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        except QueryNestError as e:
            raise HTTPException(status_code=500, detail=e.to_dict())
        return _to_query_response(result)

    @app.post("/query/multimodal", response_model=QueryResponse)
    async def query_multimodal(req: MultimodalQueryRequest):
        # API 层统一把内容收敛到底层期望的键名：image→img_path, table→table_data, equation→latex。
        # 兼容两种客户端写法：统一放在 content，或直接使用 img_path/table_data/latex。
        _KEY_BY_TYPE = {
            "image": "img_path",
            "table": "table_data",
            "equation": "latex",
        }
        content = []
        for c in req.content:
            key = _KEY_BY_TYPE.get(c.type, "content")
            raw = getattr(c, key) or c.content
            content.append({"type": c.type, key: raw})
        try:
            result = await _get_engine().query_multimodal(
                req.query, multimodal_content=content, mode=req.mode,
                system_prompt=req.system_prompt, model_id=req.model_id,
            )
        except QueryNestError as e:
            raise HTTPException(status_code=500, detail=e.to_dict())
        return _to_query_response(result)

    @app.exception_handler(QueryNestError)
    async def _qn_error_handler(request: Request, exc: QueryNestError):
        return JSONResponse(status_code=500, content=exc.to_dict())

    @app.exception_handler(RuntimeError)
    async def _runtime_error_handler(request: Request, exc: RuntimeError):
        # 引擎未配置（如 LLM/Embedding Key 缺失或 LightRAG 不可用）时，
        # 避免把裸 traceback / "Internal Server Error" 抛给使用者，返回清晰提示。
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": str(exc) or exc.__class__.__name__},
        )

    return app


def _build_default_engine(registry=None):
    """根据环境变量构建引擎；缺少 lightrag / 模型配置时给出清晰错误。"""
    from querynest import QueryNest, QueryNestConfig, engine_available

    if not engine_available():
        raise RuntimeError(
            "QueryNest 引擎不可用：缺少 lightrag 依赖。请先 `pip install lightrag`。"
        )
    cfg = QueryNestConfig()
    return QueryNest(cfg, enable_rerank=cfg.enable_rerank, model_registry=registry)


def _to_query_response(result: "RetrievalResult"):
    sources = [
        SourceOut(
            document=c.document_name or c.source,
            page=c.page,
            type=c.content_type,
            score=c.score,
            document_id=c.document_id,
            source=c.source,
            text=c.text,
        )
        for c in result.sources
    ]
    return QueryResponse(
        answer=result.answer, sources=sources,
        retrieval=result.retrieval, metadata=result.metadata,
    )


def _pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活（用于单实例锁）。"""
    if not pid:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["tasklist", "/fi", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in out.stdout
        except Exception:  # noqa: BLE001
            # 无法判定时保守视为存活，避免误覆盖他人的锁文件
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # noqa: BLE001
        return True


def _acquire_instance_lock(storage_dir: str) -> None:
    """单实例锁：防止同一存储目录被多个 ``querynest serve`` 同时写入。

    Windows 下多实例抢写 LightRAG 的 JSON 索引（如 ``kv_store_doc_status.json``）
    会触发写锁冲突 / ``PermissionError``，表现为"入库失败"。

    实现要点：
    1. 先用 ``O_CREAT | O_EXCL`` 原子创建锁文件，避免两个实例同时启动时产生竞态。
    2. 若锁文件已存在，读取 PID 并检查进程是否存活；存活则拒绝启动，否则覆盖残留死锁。
    """
    try:
        os.makedirs(storage_dir, exist_ok=True)
        lock_path = os.path.join(storage_dir, ".querynest.lock")
    except Exception:  # noqa: BLE001
        # 无法创建锁文件时不影响启动（仅失去多实例保护）
        return

    # 尝试原子创建锁文件。Windows 下 os.O_EXCL 对普通文件有效。
    fd = -1
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: _release_instance_lock(lock_path))
        return
    except FileExistsError:
        # 锁已存在：检查是活进程还是残留死锁
        pass
    except Exception:  # noqa: BLE001
        if fd != -1:
            try:
                os.close(fd)
            except Exception:  # noqa: BLE001
                pass
        return

    old_pid = 0
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            old_pid = int((f.read() or "0").strip() or "0")
    except Exception:  # noqa: BLE001
        old_pid = 0

    if old_pid and _pid_alive(old_pid):
        sys.stderr.write(
            "\n[QueryNest] 启动被拒绝：存储目录已被另一个实例占用\n"
            f"  目录: {storage_dir}\n"
            f"  已存在的实例 PID: {old_pid}\n"
            "  解决：先停止该实例，或设置不同的 QUERYNEST_STORAGE_DIR 后重试。\n\n"
        )
        sys.exit(1)

    # 残留死锁：覆盖为自己的 PID
    try:
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        atexit.register(lambda: _release_instance_lock(lock_path))
    except Exception:  # noqa: BLE001
        pass


def _release_instance_lock(lock_path: str) -> None:
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
    except Exception:  # noqa: BLE001
        pass


def serve(host: str = None, port: int = None) -> None:
    """以 uvicorn 启动服务。host/port 缺省取 QueryNestConfig。"""
    _require_fastapi()
    try:
        import uvicorn
    except Exception as e:  # noqa: BLE001
        raise QueryNestError("启动服务需要 uvicorn：`pip install uvicorn`") from e

    from querynest import QueryNestConfig

    cfg = QueryNestConfig()
    # 单实例锁：避免多个 serve 抢占同一存储目录导致入库写锁冲突
    _acquire_instance_lock(cfg.storage_dir)
    uv_config = {
        "app": "querynest.api.server:app",
        "host": host or cfg.api_host,
        "port": port or cfg.api_port,
    }
    uvicorn.run(**uv_config)


app = None
if _FASTAPI_OK:  # pragma: no cover
    app = create_app()