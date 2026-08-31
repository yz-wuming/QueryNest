"""
QueryNest — Multimodal Document Intelligence & RAG

QueryNest 是一个面向复杂文档的多模态 Retrieval-Augmented Generation (RAG) 系统。
它继承并封装了成熟的底层能力（多模态解析、LightRAG 图检索、批处理、
缓存），并新增 Query Analyzer / Query Rewrite / Hybrid Retrieval 编排 / Reranker /
Citation / Context Builder / 文档管理 / 评测框架 / CLI / FastAPI 等产品级能力。

使用示例::

    from querynest import QueryNest, QueryNestConfig

    engine = QueryNest(QueryNestConfig())
    await engine.ingest("paper.pdf")
    result = await engine.query("这个表格中哪个模型效果最好？")
"""

__version__ = "2.0.0"
__author__ = "QueryNest Team"
__url__ = "https://github.com/QueryNest/QueryNest"

# ---- 核心配置（无第三方依赖，恒可导入）------------------------------------
from .core.config import QueryNestConfig as QueryNestConfig

# ---- 统一模型 / 异常 ------------------------------------------------------
from .core.models import (
    ContextItem as ContextItem,
    DocumentMetadata as DocumentMetadata,
    RetrievalResult as RetrievalResult,
    Citation as Citation,
    ContentType as ContentType,
    Conversation as Conversation,
    Message as Message,
)
from .core.exceptions import (
    QueryNestError as QueryNestError,
    DocumentParseError as DocumentParseError,
    RetrievalError as RetrievalError,
    RerankError as RerankError,
    QueryError as QueryError,
    CitationError as CitationError,
    EvaluationError as EvaluationError,
)

# ---- Query（Analyzer / Rewrite / Citation，纯 Python）---------------------
from .query.analyzer import (
    QueryAnalyzer as QueryAnalyzer,
    QueryIntent as QueryIntent,
)
from .query.rewrite import (
    QueryRewriter as QueryRewriter,
    rewrite_query as rewrite_query,
)
from .query.citation import (
    CitationBuilder as CitationBuilder,
)

# ---- Retrieval（Hybrid / Reranker / Context，纯 Python）-------------------
from .retrieval.reranker import (
    BaseReranker as BaseReranker,
    BGEReranker as BGEReranker,
    NoopReranker as NoopReranker,
)
from .retrieval.context import (
    ContextBuilder as ContextBuilder,
)

# ---- Storage / Evaluation / API（纯 Python 或轻依赖）----------------------
from .storage.document_store import (
    DocumentStore as DocumentStore,
)
from .storage.conversation_store import (
    ConversationStore as ConversationStore,
)

# ---- 原框架低层能力（解析器 / 回调 / 韧性 / 多语言提示词）------------------
try:
    from .ingestion.parser import (
        Parser as Parser,
        register_parser as register_parser,
        unregister_parser as unregister_parser,
        list_parsers as list_parsers,
        get_supported_parsers as get_supported_parsers,
        get_parser as get_parser,
        SUPPORTED_PARSERS as SUPPORTED_PARSERS,
    )
except Exception:  # pragma: no cover - depends on third-party parsers
    pass

try:
    from .callbacks import (
        ProcessingCallback as ProcessingCallback,
        MetricsCallback as MetricsCallback,
        CallbackManager as CallbackManager,
        ProcessingEvent as ProcessingEvent,
    )
except Exception:  # pragma: no cover
    pass

try:
    from .resilience import (
        retry as retry,
        async_retry as async_retry,
        CircuitBreaker as CircuitBreaker,
    )
except Exception:  # pragma: no cover
    pass

try:
    from .prompt_manager import (
        set_prompt_language as set_prompt_language,
        get_prompt_language as get_prompt_language,
        reset_prompts as reset_prompts,
        register_prompt_language as register_prompt_language,
        get_available_languages as get_available_languages,
    )
except Exception:  # pragma: no cover
    pass

# ---- 核心引擎（需要 lightrag）----------------------------------------------
try:
    from .core.engine import QueryNest as QueryNest
except Exception as _engine_error:  # pragma: no cover - 需要 lightrag 才能导入
    QueryNest = None
    _QUERYNEST_ENGINE_IMPORT_ERROR = _engine_error


def get_version() -> str:
    """Return the QueryNest version string."""
    return __version__


def engine_available() -> bool:
    """Whether the full QueryNest engine (and its LightRAG dependency) is importable."""
    return QueryNest is not None


__all__ = [
    "QueryNest",
    "QueryNestConfig",
    # models
    "ContentType",
    "ContextItem",
    "DocumentMetadata",
    "RetrievalResult",
    "Citation",
    "Conversation",
    "Message",
    # exceptions
    "QueryNestError",
    "DocumentParseError",
    "RetrievalError",
    "RerankError",
    "QueryError",
    "CitationError",
    "EvaluationError",
    # query
    "QueryAnalyzer",
    "QueryIntent",
    "QueryRewriter",
    "rewrite_query",
    "CitationBuilder",
    # retrieval
    "BaseReranker",
    "BGEReranker",
    "NoopReranker",
    "ContextBuilder",
    # storage
    "DocumentStore",
    "ConversationStore",
    # 原框架能力（存在才导出）
]

if "Parser" in globals():
    __all__.extend(["Parser", "register_parser", "unregister_parser",
                    "list_parsers", "get_supported_parsers", "get_parser",
                    "SUPPORTED_PARSERS"])
if "ProcessingCallback" in globals():
    __all__.extend(["ProcessingCallback", "MetricsCallback", "CallbackManager",
                    "ProcessingEvent"])
if "retry" in globals():
    __all__.extend(["retry", "async_retry", "CircuitBreaker"])
if "set_prompt_language" in globals():
    __all__.extend(["set_prompt_language", "get_prompt_language", "reset_prompts",
                    "register_prompt_language", "get_available_languages"])
if "QueryNest" in globals():
    __all__.append("QueryNest")