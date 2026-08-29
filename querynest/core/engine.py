"""
QueryNest 核心引擎

整合多层能力：
- **继承**：多模态解析（MinerU/Docling/PaddleOCR）、多模态处理器（image/table/equation）、
  LightRAG 图-RAG 检索、批处理与增量索引、三层缓存、回调/重试工具
- **新增**：Query Analyzer / Rewrite / Hybrid Retrieval 编排 / Reranker / Citation /
  Context Builder / Document Management / Evaluation
"""

import asyncio
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from lightrag import LightRAG, QueryParam
from lightrag.utils import EmbeddingFunc, logger

from querynest.core.config import QueryNestConfig
from querynest.core.models import (
    Citation,
    ContentType,
    ContextItem,
    DocumentMetadata,
    RetrievalResult,
)
from querynest.core.exceptions import (
    DocumentParseError,
    QueryError,
)
from querynest.query.analyzer import QueryAnalyzer, QueryIntent
from querynest.query.rewrite import QueryRewriter, Turn
from querynest.query.citation import CitationBuilder
from querynest.query.base import QueryMixin
from querynest.retrieval.reranker import BaseReranker, NoopReranker
from querynest.retrieval.hybrid import HybridRetriever, FunctionRetriever
from querynest.retrieval.context import ContextBuilder as ContextBuilder_
from querynest.ingestion.processor import ProcessorMixin
from querynest.storage.document_store import DocumentStore
from querynest.core.logging import get_logger


class QueryNest:
    """QueryNest 核心引擎——多模态文档智能与 RAG 入口。

    使用示例::

        from querynest import QueryNest, QueryNestConfig

        config = QueryNestConfig()
        engine = QueryNest(config, llm_model_func=..., embedding_func=...)

        # 文档入库
        await engine.ingest("paper.pdf")

        # 查询
        result = await engine.query("表格中哪个模型效果最好？")
        print(result.answer)
        for src in result.sources:
            print(f"  [{src.document_name}] Page {src.page}")
    """

    def __init__(
        self,
        config: QueryNestConfig,
        llm_model_func: Optional[Callable] = None,
        vision_model_func: Optional[Callable] = None,
        embedding_func: Optional[Callable] = None,
        lightrag: Optional[LightRAG] = None,
        lightrag_kwargs: Optional[Dict[str, Any]] = None,
        reranker: Optional[BaseReranker] = None,
        query_analyzer: Optional[QueryAnalyzer] = None,
        enable_rerank: bool = False,
    ):
        self.config = config
        self.llm_model_func = llm_model_func
        self.vision_model_func = vision_model_func
        self.embedding_func = embedding_func
        self.lightrag_kwargs = lightrag_kwargs or {}

        # --- 继承的底层引擎 ---
        self._lightrag = lightrag
        self._rag_engine = None  # 内部 RAG-Anything 兼容组合（延迟初始化）
        self._parse_cache = None
        self._multimodal_status_cache = None
        self._modal_processors = {}
        self._context_extractor = None
        self._parser = None
        self._doc_parser = None
        self._parser_installation_checked = False

        # --- 新增的 QueryNest 能力 ---
        self.reranker = reranker or NoopReranker()
        self.enable_rerank = enable_rerank
        self.query_analyzer = query_analyzer or QueryAnalyzer(
            llm_func=llm_model_func if hasattr(llm_model_func, "__call__") else None
        )
        self.query_rewriter = QueryRewriter(llm_func=llm_model_func)
        self.citation_builder = CitationBuilder()
        self.context_builder = ContextBuilder_()
        self.document_store = DocumentStore(
            storage_dir=str(config.storage_dir or config.working_dir)
        )
        self.logger = get_logger("querynest")

        # 初始化检索器（延迟构建，在 _ensure_lightrag 后真正可用）
        self._hybrid_retriever = None

    # ================================================================ #
    # 公共 API
    # ================================================================ #

    async def ingest(
        self,
        file_path: str,
        parse_method: Optional[str] = None,
        display_stats: Optional[bool] = None,
        **kwargs,
    ) -> DocumentMetadata:
        """文档入库：解析 → 多模态理解 → 索引 → 元数据记录。

        Args:
            file_path: 文档路径或 URL。
            parse_method: 解析方法 ('auto', 'ocr', 'txt')，默认 config.parse_method。
            display_stats: 是否显示解析统计。

        Returns:
            DocumentMetadata: 入库文档元数据（含 doc_id）。
        """
        await self._ensure_initialized()
        # 使用内部 process_document_complete（继承自 ProcessorMixin）
        doc_id = kwargs.pop("doc_id", None)
        await self._rag_engine.process_document_complete(
            file_path,
            parse_method=parse_method or self.config.parse_method,
            display_stats=display_stats if display_stats is not None else self.config.display_content_stats,
            doc_id=doc_id,
            **kwargs,
        )
        # 构造元数据并记录到 document_store。
        # process_document_complete 不 return content_list，解析产物改为从其
        # 实例状态读取（PDF Index Bridge 增量字段）。
        content_list = list(
            getattr(self._rag_engine, "_last_parse_content_list", []) or []
        )
        resolved_doc_id = getattr(self._rag_engine, "_last_parse_doc_id", None) or doc_id or file_path

        meta = DocumentMetadata(
            document_id=resolved_doc_id,
            filename=Path(file_path).name,
            file_type=Path(file_path).suffix.lstrip("."),
            parser=self.config.parser,
            parse_method=parse_method or self.config.parse_method,
            source_path=file_path,
        )
        # PDF Index Bridge 修复：把解析正文作为文档检索源（source）存盘，让 BM25
        # 读正文。否则 source_path 仍指向原始 PDF，BM25 会把二进制当文本读出泄漏。
        text_source = ""
        if isinstance(content_list, list) and content_list:
            from querynest.utils import separate_content

            text_content, _ = separate_content(content_list)
            text_source = text_content or ""
        self.document_store.upsert(meta, content=(text_source or None))
        return meta

    async def query(
        self,
        query: str,
        mode: str = "mix",
        top_k: int = 20,
        history: Optional[List[Dict[str, str]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> RetrievalResult:
        """统一查询入口。

        流程：Query Analyzer → Query Rewrite → Hybrid Retrieval → Rerank →
        Context Builder → LLM Generation → Citation。

        Args:
            query: 用户问题。
            mode: 底层 LightRAG 查询模式。
            top_k: 检索命中数上限。
            history: 可选的多轮对话历史 [{"user":..., "assistant":...}, ...]。
            system_prompt: 可选系统提示词。

        Returns:
            RetrievalResult: 包含 answer / sources / retrieval / metadata。
        """
        await self._ensure_initialized()

        # 1) Query Analyzer
        intent = self.query_analyzer.classify(query)
        self.logger.info(f"Query intent: {intent.value}")

        # 2) Query Rewrite
        turns = []
        if history:
            for h in history:
                turns.append(Turn(user=h.get("user", ""), assistant=h.get("assistant", "")))
        rewritten = self.query_rewriter.rewrite(query, history=turns)
        if rewritten != query:
            self.logger.info(f"Query rewritten: {query[:60]} -> {rewritten[:60]}")

        # 3) Hybrid Retrieval
        use_vlm = (
            self.vision_model_func is not None
            and intent in (ContentType.IMAGE, ContentType.MULTIMODAL)
        )

        if use_vlm:
            # VLM 增强路径：走 LightRAG 检索 prompt 后替换图片
            return await self._query_vlm_enhanced(
                rewritten, mode=mode, system_prompt=system_prompt, **kwargs
            )

        # 标准路径：HybridRetriever → Rerank → Context → LLM
        # 底层 dense/graph 检索器是 async 的，走 retrieve_async 拿到真实命中的列表。
        hits = await self._hybrid_retriever.retrieve_async(rewritten, top_k=top_k)
        retrieval_meta = {
            "intent": intent.value,
            "rewritten": rewritten if rewritten != query else None,
            "num_hits": len(hits),
            "mode": mode,
        }

        # 4) Rerank
        if self.enable_rerank and hits:
            reranked = self.reranker.rerank(rewritten, hits, top_k=min(len(hits), top_k))
            if reranked:
                hits = [hits[i] for i, _ in reranked]
                retrieval_meta["reranked"] = True

        # 5) Context Builder
        context_items = self.context_builder.build(hits)

        # 6) LLM 生成
        context_text = self.context_builder.render(context_items)
        if context_text:
            llm_context = f"检索到的上下文:\n{context_text}"
        else:
            llm_context = ""

        try:
            answer = await self._rag_engine.aquery(
                rewritten,
                mode=mode,
                system_prompt=system_prompt,
                **kwargs,
            )
        except Exception as e:
            raise QueryError(f"LLM 生成失败: {e}") from e

        # 7) Citation
        sources = self.citation_builder.build(hits)

        return RetrievalResult(
            answer=answer,
            sources=sources,
            retrieval=retrieval_meta,
            metadata={"intent": intent.value, "rewritten": rewritten != query and rewritten or None},
        )

    async def query_multimodal(
        self,
        query: str,
        multimodal_content: List[Dict[str, Any]],
        mode: str = "mix",
        system_prompt: Optional[str] = None,
        **kwargs,
    ) -> RetrievalResult:
        """多模态查询：直接传入图片/表格/公式等内容。

        Args:
            query: 用户问题。
            multimodal_content: [{"type": "image", "img_path": "..."}, ...]。
            mode: 查询模式。

        Returns:
            RetrievalResult: 结果。
        """
        await self._ensure_initialized()

        # ---- 直通 VLM：请求带本地图片 + 引擎配置了视觉模型时，直接把图片喂给 VLM 看图回答 ----
        images = [
            m for m in multimodal_content
            if isinstance(m, dict) and m.get("type") == "image" and m.get("img_path")
        ]
        if images and self.vision_model_func is not None:
            try:
                from querynest.utils import encode_image_to_base64

                content_parts: List[Dict[str, Any]] = []
                for m in images:
                    base64_img = encode_image_to_base64(m["img_path"])
                    if base64_img:
                        content_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_img}"
                                },
                            }
                        )
                if content_parts:
                    content_parts.append(
                        {"type": "text", "text": query}
                    )
                    sys_prompt = (
                        system_prompt
                        or "你是一个多模态文档智能助手。请根据用户提供的图片与问题，仔细观察图片内容并给出准确、具体的回答。"
                    )
                    answer = self.vision_model_func(
                        "",
                        messages=[
                            {"role": "system", "content": sys_prompt},
                            {"role": "user", "content": content_parts},
                        ],
                    )
                    self.logger.info(
                        f"[multimodal] 直通 VLM 处理 {len(content_parts) - 1} 张图片"
                    )
                    return RetrievalResult(
                        answer=answer,
                        retrieval={"mode": "vlm", "multimodal": True, "images": len(images)},
                    )
            except Exception as exc:  # noqa: BLE001
                self.logger.warning(
                    f"[multimodal] 直通 VLM 失败，回退到检索路径: {exc}"
                )

        if not hasattr(self._rag_engine, "aquery_with_multimodal"):
            raise QueryError("引擎不支持多模态查询（需要 ProcessorMixin 能力）")

        answer = await self._rag_engine.aquery_with_multimodal(
            query,
            multimodal_content=multimodal_content,
            mode=mode,
            system_prompt=system_prompt,
            **kwargs,
        )
        return RetrievalResult(
            answer=answer,
            retrieval={"mode": mode, "multimodal": True},
        )

    # ================================================================ #
    # 文档管理
    # ================================================================ #

    def list_documents(self, limit: int = 1000) -> List[Dict[str, Any]]:
        return self.document_store.list_documents(limit=limit)

    def get_document(self, document_id: str) -> Dict[str, Any]:
        return self.document_store.get_document(document_id)

    def delete_document(self, document_id: str) -> bool:
        return self.document_store.delete_document(document_id)

    def document_exists(self, document_id: str) -> bool:
        return self.document_store.document_exists(document_id)

    def document_status(self, document_id: str) -> str:
        return self.document_store.document_status(document_id)

    # ================================================================ #
    # 内部初始化
    # ================================================================ #

    async def _ensure_initialized(self):
        """确保底层引擎与检索器就绪（延迟初始化，仅在首次调用时触发）。"""
        if self._hybrid_retriever is not None:
            return

        # 委托给内部 RAGAnything 初始化
        await self._init_rag_engine()

        # 构建 HybridRetriever
        dense_retriever = self._build_dense_retriever()
        graph_retriever = self._build_graph_retriever()
        keyword_retriever = self._build_keyword_retriever()
        self._hybrid_retriever = HybridRetriever(
            dense=dense_retriever,
            keyword=keyword_retriever,
            graph=graph_retriever,
            reranker=self.reranker if self.enable_rerank else None,
            enable_rerank=self.enable_rerank,
        )

    def _build_lightrag_embedding_func(self) -> Optional[EmbeddingFunc]:
        """把 QueryNest 的 embedding 回调封装成 LightRAG 1.5.x 需要的 ``EmbeddingFunc``。

        LightRAG 1.5.x 要求 ``embedding_func`` 是 ``EmbeddingFunc`` 实例（含 ``.func``），
        不能直接传裸函数；嵌入函数会被 ``await`` 调用。这里统一转为 async 适配。
        若配置未声明 ``embedding_dim``，用一次轻量探测确定维度（保证向量库正确）。
        """
        if self.embedding_func is None:
            return None
        if isinstance(self.embedding_func, EmbeddingFunc):
            return self.embedding_func
        embedding_dim = int(getattr(self.config, "embedding_dim", 0) or 0)
        if not embedding_dim:
            try:
                probe = _safe_call(self.embedding_func, ["probe"])
                if probe is not None and len(probe):
                    embedding_dim = int(len(probe[0]))
            except Exception as e:  # noqa: BLE001
                self.logger.warning(
                    f"无法探测 embedding 维度，使用占位 4: {e}"
                )
                embedding_dim = 4
        return EmbeddingFunc(
            func=_as_async(self.embedding_func),
            embedding_dim=embedding_dim,
            model_name=getattr(self.config, "embedding_model", None),
        )

    def _ensure_client_funcs(self) -> None:
        """未显式传入 LLM/Embedding 回调时，按 config（QUERYNEST_*/.env）自动构建。

        CLI / API / quickstart 等入口通常只传 config；这里保证"配置好 .env 即可真实运行"。
        显式传入的回调不会被覆盖；构建失败仅记录 warning，由后续初始化给出明确提示。
        """
        if self.llm_model_func is not None and self.embedding_func is not None:
            pass  # LLM/Embedding 已显式提供，继续按需补 Vision
        try:
            from querynest.core.clients import (
                build_openai_embedding_func,
                build_openai_llm_func,
                build_vision_model_func,
            )

            if self.llm_model_func is None:
                self.llm_model_func = build_openai_llm_func(self.config)
            if self.embedding_func is None:
                self.embedding_func = build_openai_embedding_func(self.config)
            # Vision：作为可选增强能力自动构建（复用同一 base_url + API Key）。
            # 若视觉调用失败会由上层 graceful fallback，不影响文本检索。
            if self.vision_model_func is None:
                self.vision_model_func = build_vision_model_func(self.config)
        except Exception as e:  # noqa: BLE001
            self.logger.warning(
                "自动构建 LLM/Embedding/Visual 回调失败（初始化时将给出明确提示）: %s", e
            )

    async def _init_rag_engine(self):
        """初始化内部 RAG-Anything 组合（整套流水线）。"""
        from querynest.ingestion.parser import get_parser

        self._ensure_client_funcs()

        # 创建配置供内部使用（兼容旧接口）
        working_dir = self.config.storage_dir or self.config.working_dir
        if not os.path.exists(working_dir):
            os.makedirs(working_dir)

        # 解析器
        self._doc_parser = get_parser(self.config.parser)

        # LightRAG 初始化
        if self._lightrag is None:
            if self.llm_model_func is None or self.embedding_func is None:
                raise RuntimeError(
                    "使用 QueryNest 需要 llm_model_func 和 embedding_func，"
                    "或直接传入预初始化的 LightRAG 实例"
                )
            lightrag_params = {
                "working_dir": working_dir,
                "llm_model_func": _as_async(self.llm_model_func),
                "embedding_func": self._build_lightrag_embedding_func(),
            }
            lightrag_params.update(self.lightrag_kwargs)
            # 新版 LightRAG 通过 embedding_func 返回的向量维度自动推断 embedding_dim，
            # 因此无需（也不能）在构造参数里显式传 embedding_dim。
            self._lightrag = LightRAG(**lightrag_params)
            await self._lightrag.initialize_storages()

        # 创建轻量 RAG 适配器，复用 ProcessorMixin / QueryMixin 能力
        self._rag_engine = _RAGAdapter(
            lightrag=self._lightrag,
            config=self.config,
            llm_model_func=self.llm_model_func,
            vision_model_func=self.vision_model_func,
            embedding_func=self.embedding_func,
            doc_parser=self._doc_parser,
        )
        init_result = await self._rag_engine._ensure_lightrag_initialized()
        if not init_result or not init_result.get("success"):
            raise RuntimeError(f"引擎初始化失败: {(init_result or {}).get('error', 'unknown')}")

    def _build_dense_retriever(self):
        """Dense（向量）检索器：调用 LightRAG aquery 只需要 prompt 以获取命中。"""

        async def retrieve(query: str, top_k: int = 20) -> List[Dict[str, Any]]:
            if self._lightrag is None:
                return []
            param = QueryParam(mode="local", only_need_prompt=True, top_k=top_k)
            try:
                # 只获取检索上下文（prompt），不生成回答，解析出命中的 chunks
                prompt = await self._lightrag.aquery(query, param=param)
                # 提取命中信息（依赖 LightRAG prompt 格式，通过正则解析标记）
                return _extract_hits_from_prompt(prompt)
            except Exception as e:  # noqa: BLE001
                # 单路失败不拖垮整次查询（如 LLM 限流），记录警告并返回空。
                self.logger.warning(f"Dense retrieval 失败，返回空（graceful）：{e!s}")
                return []

        return FunctionRetriever(retrieve, name="dense", is_async=True)

    def _build_graph_retriever(self):
        """Graph（知识图）检索器：调用 LightRAG global 模式获取图命中。"""

        async def retrieve(query: str, top_k: int = 20) -> List[Dict[str, Any]]:
            if self._lightrag is None:
                return []
            param = QueryParam(mode="global", only_need_prompt=True, top_k=top_k)
            try:
                prompt = await self._lightrag.aquery(query, param=param)
                return _extract_hits_from_prompt(prompt)
            except Exception as e:  # noqa: BLE001
                # 图数据当前为 0 或 LLM 限流时，单路失败不拖垮 Dense+BM25。
                self.logger.warning(f"Graph retrieval 失败，返回空（graceful）：{e!s}")
                return []

        return FunctionRetriever(retrieve, name="graph", is_async=True)

    def _build_keyword_retriever(self):
        """Keyword（BM25）检索器：在 DocumentStore 已存来源正文上做 BM25 召回。

        优先读 ``document_store.read_source(doc_id)``（已落盘的解析正文文件，
        PDF Index Bridge 修复）；无落盘正文时回退 ``_read_source``（带二进制守卫）。
        """
        from querynest.retrieval.keyword import BM25Retriever, _read_source

        def retrieve(query: str, top_k: int = 20) -> List[Dict[str, Any]]:
            try:
                docs = self.document_store.list_documents()
            except Exception:  # noqa: BLE001
                return []
            corpus: List[Dict[str, Any]] = []
            for d in docs:
                doc_id = d.get("document_id") or d.get("id")
                body = self.document_store.read_source(doc_id) if doc_id else ""
                if not body:
                    # 兜底：无落盘正文时读 source_path（已对二进制守卫，不会泄漏）
                    body = _read_source(d)
                if not body:
                    continue
                corpus.append(
                    {
                        "document_id": doc_id,
                        "document_name": d.get("filename") or doc_id,
                        "text": body,
                        "source_path": d.get("source_path", ""),
                        "page": d.get("page_count", 0),
                    }
                )
            if not corpus:
                return []
            return BM25Retriever(corpus).retrieve(query, top_k=top_k)

        return FunctionRetriever(retrieve, name="keyword")

    async def _query_vlm_enhanced(self, query, mode="mix", system_prompt=None, **kwargs):
        """VLM 增强查询（图片上下文）。"""
        if not hasattr(self._rag_engine, "aquery_vlm_enhanced"):
            raise QueryError("VLM 增强查询需要 vision_model_func")
        answer = await self._rag_engine.aquery_vlm_enhanced(
            query, mode=mode, system_prompt=system_prompt, **kwargs
        )
        sources = getattr(self._rag_engine, "_current_images_base64", [])
        return RetrievalResult(
            answer=answer,
            retrieval={"mode": mode, "vlm_enhanced": True, "images": len(sources)},
            metadata={"intent": "image"},
        )

    def close(self):
        """清理资源。"""
        if hasattr(self._rag_engine, "close"):
            self._rag_engine.close()


# ==================================================================== #
# 内部适配器：把 ProcessorMixin / QueryMixin 组合成一个可调用对象
# ==================================================================== #

class _RAGAdapter(ProcessorMixin, QueryMixin):
    """内部适配器，组合继承的 RAG-Anything 能力。

    对外暴露与旧 ProcessorMixin + QueryMixin 兼容的接口，让 QueryNest.ingest
    和 QueryNest.query 能复用稳定代码而无需重写。继承 ``ProcessorMixin`` 从而
    直接获得 ``process_document_complete`` / ``parse_document`` 等完整流水线，
    避免在适配器里手动重建（否则会缺 ``process_document`` 等实例方法）。
    """

    def __init__(self, lightrag, config, llm_model_func=None,
                 vision_model_func=None, embedding_func=None, doc_parser=None):
        self.lightrag = lightrag
        self.config = config
        self.llm_model_func = llm_model_func
        self.vision_model_func = vision_model_func
        self.embedding_func = embedding_func
        self.doc_parser = doc_parser
        self.logger = logger
        self.modal_processors = {}
        self.context_extractor = None
        self.parse_cache = None
        self.multimodal_status_cache = None
        self._parser_installation_checked = False

    async def _ensure_lightrag_initialized(self):
        """转发到 ProcessorMixin 的同名方法（通过 __init__ 的懒初始化）。"""
        # 最小化初始化：parse_cache + multimodal_status_cache
        if self.lightrag is not None:
            self.parse_cache = self.lightrag.key_string_value_json_storage_cls(
                namespace="parse_cache",
                workspace=self.lightrag.workspace,
                global_config=self.lightrag.__dict__,
                embedding_func=self.embedding_func,
            )
            await self.parse_cache.initialize()
            self.multimodal_status_cache = self.lightrag.key_string_value_json_storage_cls(
                namespace="multimodal_status",
                workspace=self.lightrag.workspace,
                global_config=self.lightrag.__dict__,
                embedding_func=self.embedding_func,
            )
            await self.multimodal_status_cache.initialize()
            return {"success": True}
        return {"success": False, "error": "No LightRAG instance"}

    async def aquery(self, query, mode="mix", system_prompt=None, **kwargs):
        """委托给 LightRAG 的 aquery。"""
        if self.lightrag is None:
            raise RuntimeError("LightRAG not initialized")
        param = QueryParam(mode=mode, **kwargs)
        return await self.lightrag.aquery(query, param=param, system_prompt=system_prompt)

    def set_content_source_for_context(self, content_source, content_format: str = "auto"):
        """把解析出的内容设置为各 modal processor 的上下文来源（兼容旧接口）。"""
        if not self.modal_processors:
            return
        for processor in self.modal_processors.values():
            try:
                setter = getattr(processor, "set_content_source", None)
                if setter is not None:
                    setter(content_source, content_format)
            except Exception as e:  # noqa: BLE001
                self.logger.warning(f"Failed to set content source for modal processor: {e!s}")

    async def close(self):
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self._finalize())
        except RuntimeError:
            try:
                asyncio.run(self._finalize())
            except Exception:
                pass

    async def _finalize(self):
        tasks = []
        if self.parse_cache is not None:
            tasks.append(self.parse_cache.finalize())
        if self.multimodal_status_cache is not None:
            tasks.append(self.multimodal_status_cache.finalize())
        if self.lightrag is not None and hasattr(self.lightrag, "finalize_storages"):
            tasks.append(self.lightrag.finalize_storages())
        if tasks:
            import asyncio
            await asyncio.gather(*tasks)


# ==================================================================== #
# 辅助函数：从 LightRAG 检索 prompt 中提取命中信息
# ==================================================================== #

# LightRAG 在 only_need_prompt=True 时返回的 context 文本里，若带了来源编号，
# 通常以 "[1]"、"[2]" 等标记出现；我们用这组正则做尽力而为的解析。
_CTX_INDEX_RE = re.compile(r"\[(\d+)\]")
_CTX_BULLET_RE = re.compile(r"^[-*]\s+", re.MULTILINE)
_CTX_NUM_RE = re.compile(r"^\d+\.\s+", re.MULTILINE)


def _extract_hits_from_prompt(prompt: Any) -> List[Dict[str, Any]]:
    """从 LightRAG ``only_need_prompt=True`` 的检索结果中尽力提取命中。

    LightRAG 公开 API 不直接返回结构化命中，这里把检索到的上下文文本封装为
    一个聚合命中，供下游 Hybrid Retrieval 融合、Rerank、Context 与 Citation
    使用。无法结构化或文本为空时返回空列表（绝不伪造命中）。

    Args:
        prompt: LightRAG 返回的检索提示文本（或 None）。

    Returns:
        List[Dict]: ``[{"content": ..., "content_type": "text",
                                    "source": "lightrag", "score": ..., ...}]``
    """
    if not isinstance(prompt, str) or not prompt.strip():
        return []
    text = prompt.strip()
    first_line = text.splitlines()[0]
    hint = first_line[:80] if first_line else ""
    return [
        {
            "content": text,
            "content_type": "text",
            "source": "lightrag",
            "score": 1.0,
            "reranked": False,
            "metadata": {"hint": hint},
        }
    ]


def _as_async(func: Optional[Callable]) -> Optional[Callable]:
    """把同步/异步 callable 统一为 async，供 LightRAG 直接 ``await``。"""
    if func is None:
        return None
    if asyncio.iscoroutinefunction(func):
        return func

    async def _wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            return await result
        return result

    return _wrapper


def _safe_call(func: Callable, texts: List[str]) -> Any:
    """同步/异步调用嵌入函数（用于探测向量维度等轻量场景）。"""
    result = func(texts)
    if asyncio.iscoroutine(result):
        return asyncio.run(result) if not asyncio.get_event_loop().is_running() else None
    return result