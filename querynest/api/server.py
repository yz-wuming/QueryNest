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

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from querynest.core.exceptions import (
    DocumentNotFoundError,
    QueryNestError,
    RetrievalError,
)
from querynest.core.models import RetrievalResult

try:  # pragma: no cover - depends on fastapi
    from fastapi import FastAPI, HTTPException, Request
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


def create_app(engine: Any = None) -> "FastAPI":
    """创建 FastAPI 应用。

    Args:
        engine: 可选的特性注入（提供 ``ingest/query/query_multimodal/
            list_documents/get_document/delete_document``）。默认延迟构建。
    """
    _require_fastapi()

    app = FastAPI(
        title="QueryNest - Multimodal Document Intelligence & RAG",
        version="2.0.0",
        description="面向复杂文档的多模态 Retrieval-Augmented Generation 系统",
    )
    app.state.engine = engine

    def _get_engine():
        eng = app.state.engine
        if eng is None:
            eng = _build_default_engine()
            app.state.engine = eng
        return eng

    # ---------------- health ----------------
    @app.get("/", include_in_schema=False)
    def chat_home():
        return HTMLResponse(_load_app_html())

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
        try:
            meta = await _get_engine().ingest(path, parse_method=parse_method, doc_id=doc_id)
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
        try:
            meta = await _get_engine().ingest(str(dest.resolve()), parse_method=parse_method)
        except QueryNestError as e:
            raise HTTPException(status_code=422, detail=e.to_dict())
        return {"document_id": meta.document_id, "document": meta.to_dict()}

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

    # ---------------- query ----------------
    @app.post("/query", response_model=QueryResponse)
    async def query(req: QueryRequest):
        try:
            result = await _get_engine().query(
                req.query,
                mode=req.mode,
                top_k=req.top_k,
                history=req.history,
                system_prompt=req.system_prompt,
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
                system_prompt=req.system_prompt,
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


def _build_default_engine():
    """根据环境变量构建引擎；缺少 lightrag / 模型配置时给出清晰错误。"""
    from querynest import QueryNest, QueryNestConfig, engine_available

    if not engine_available():
        raise RuntimeError(
            "QueryNest 引擎不可用：缺少 lightrag 依赖。请先 `pip install lightrag`。"
        )
    cfg = QueryNestConfig()
    return QueryNest(cfg, enable_rerank=cfg.enable_rerank)


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


def serve(host: str = None, port: int = None) -> None:
    """以 uvicorn 启动服务。host/port 缺省取 QueryNestConfig。"""
    _require_fastapi()
    try:
        import uvicorn
    except Exception as e:  # noqa: BLE001
        raise QueryNestError("启动服务需要 uvicorn：`pip install uvicorn`") from e

    from querynest import QueryNestConfig

    cfg = QueryNestConfig()
    uv_config = {
        "app": "querynest.api.server:app",
        "host": host or cfg.api_host,
        "port": port or cfg.api_port,
    }
    uvicorn.run(**uv_config)


app = None
if _FASTAPI_OK:  # pragma: no cover
    app = create_app()