"""
QueryNest 配置系统

统一使用 ``QUERYNEST_`` 前缀的环境变量，并在未设置时回退读取旧的
兼容变量名（见 ``_LEGACY_MAP``）以保持对历史配置的兼容。

本模块不依赖任何第三方库，可独立导入用于测试。
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


def _env(name: str, default=None, cast=None):
    """读取环境变量，优先读 QUERYNEST_ 前缀，其次读旧名称回退。

    - 首选 ``QUERYNEST_<NAME>``（NAME 即传入的短名）。
    - 若未设置，回退到旧变量名（由 legacy 提供，可为同一名称或旧前缀）。
    """
    # 兼容：有些变量历史上就是独立的（如 LLM_MODEL），走 QUERYNEST_ 前缀即可
    primary_candidates = [f"QUERYNEST_{name}", "QUERYNEST_" + name]
    legacy_candidates: List[str] = _LEGACY_MAP.get(name, [name])

    raw: Optional[str] = None
    for key in (*primary_candidates, *legacy_candidates):
        val = os.environ.get(key)
        if val is not None and val != "":
            raw = val
            break

    if raw is None:
        return default

    if cast is None:
        return raw
    if cast is bool:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if cast is int:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    if cast is float:
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default
    return cast(raw)


# 旧变量名回退映射：短名 -> 候选旧环境变量名
_LEGACY_MAP = {
    "WORKING_DIR": ["WORKING_DIR"],
    "PARSE_METHOD": ["PARSE_METHOD", "MINERU_PARSE_METHOD"],
    "OUTPUT_DIR": ["OUTPUT_DIR", "QUERYNEST_PARSER_OUTPUT_DIR", "PARSER_OUTPUT_DIR"],
    "PARSER": ["PARSER"],
    "DISPLAY_CONTENT_STATS": ["DISPLAY_CONTENT_STATS"],
    "ENABLE_IMAGE_PROCESSING": ["ENABLE_IMAGE_PROCESSING"],
    "ENABLE_TABLE_PROCESSING": ["ENABLE_TABLE_PROCESSING"],
    "ENABLE_EQUATION_PROCESSING": ["ENABLE_EQUATION_PROCESSING"],
    "MAX_CONCURRENT_FILES": ["MAX_CONCURRENT_FILES"],
    "SUPPORTED_FILE_EXTENSIONS": ["SUPPORTED_FILE_EXTENSIONS"],
    "RECURSIVE_FOLDER_PROCESSING": ["RECURSIVE_FOLDER_PROCESSING"],
    "CONTEXT_WINDOW": ["CONTEXT_WINDOW"],
    "CONTEXT_MODE": ["CONTEXT_MODE"],
    "MAX_CONTEXT_TOKENS": ["MAX_CONTEXT_TOKENS"],
    "INCLUDE_HEADERS": ["INCLUDE_HEADERS"],
    "INCLUDE_CAPTIONS": ["INCLUDE_CAPTIONS"],
    "CONTEXT_FILTER_CONTENT_TYPES": ["CONTEXT_FILTER_CONTENT_TYPES"],
    "CONTENT_FORMAT": ["CONTENT_FORMAT"],
    "USE_FULL_PATH": ["USE_FULL_PATH"],
    # 模型 / 检索配置（新增）
    "LLM_API_KEY": ["LLM_BINDING_API_KEY", "LLM_API_KEY"],
    "LLM_BASE_URL": ["LLM_BINDING_HOST", "OPENAI_BASE_URL", "LLM_BASE_URL"],
    "LLM_MODEL": ["LLM_MODEL"],
    "LLM_BINDING": ["LLM_BINDING"],
    "LLM_TEMPERATURE": ["TEMPERATURE", "LLM_TEMPERATURE"],
    "LLM_MAX_TOKENS": ["MAX_TOKENS", "LLM_MAX_TOKENS"],
    "EMBEDDING_MODEL": ["EMBEDDING_MODEL"],
    "EMBEDDING_BINDING": ["EMBEDDING_BINDING"],
    "EMBEDDING_BINDING_HOST": ["EMBEDDING_BINDING_HOST"],
    "EMBEDDING_BINDING_API_KEY": ["EMBEDDING_BINDING_API_KEY"],
    "EMBEDDING_DIM": ["EMBEDDING_DIM"],
    "VISION_MODEL": ["VISION_MODEL"],
    "RERANKER_MODEL": ["RERANKER_MODEL", "QUERYNEST_RERANKER_MODEL"],
    "STORAGE_DIR": ["WORKING_DIR", "STROAGE_DIR", "STORAGE_DIR"],
    "PARSER_DEVICE": ["DEVICE", "PARSER_DEVICE"],
    "ENABLE_RERANK": ["ENABLE_RERANK", "ENABLE_RERANKER"],
    "QUERY_TOP_K": ["QUERY_TOP_K", "TOP_K"],
    "LOG_LEVEL": ["LOG_LEVEL"],
    "API_HOST": ["HOST"],
    "API_PORT": ["PORT"],
}


DEFAULT_EXTENSIONS = (
    ".pdf,.jpg,.jpeg,.png,.bmp,.tiff,.tif,.gif,.webp,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.txt,.md,.json"
)
DEFAULT_WORKING_DIR = "./querynest_storage"


@dataclass
class QueryNestConfig:
    """QueryNest 配置，全部支持 ``QUERYNEST_*`` 环境变量覆盖。"""

    # ---- 目录 ----
    working_dir: str = field(
        default_factory=lambda: _env("WORKING_DIR", DEFAULT_WORKING_DIR, str)
    )
    storage_dir: str = field(
        default_factory=lambda: _env("STORAGE_DIR", "./querynest_storage", str)
    )
    parser_output_dir: str = field(
        default_factory=lambda: _env("OUTPUT_DIR", "./querynest_output", str)
    )

    # ---- 解析 ----
    parser: str = field(default_factory=lambda: _env("PARSER", "mineru", str))
    parse_method: str = field(default_factory=lambda: _env("PARSE_METHOD", "auto", str))
    display_content_stats: bool = field(
        default_factory=lambda: _env("DISPLAY_CONTENT_STATS", True, bool)
    )
    parser_device: str = field(default_factory=lambda: _env("PARSER_DEVICE", "cpu", str))

    # ---- 多模态处理开关 ----
    enable_image_processing: bool = field(
        default_factory=lambda: _env("ENABLE_IMAGE_PROCESSING", True, bool)
    )
    enable_table_processing: bool = field(
        default_factory=lambda: _env("ENABLE_TABLE_PROCESSING", True, bool)
    )
    enable_equation_processing: bool = field(
        default_factory=lambda: _env("ENABLE_EQUATION_PROCESSING", True, bool)
    )

    # ---- 批处理 ----
    max_concurrent_files: int = field(
        default_factory=lambda: _env("MAX_CONCURRENT_FILES", 1, int)
    )
    supported_file_extensions: List[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in _env(
                "SUPPORTED_FILE_EXTENSIONS", DEFAULT_EXTENSIONS, str
            ).split(",")
        ]
    )
    recursive_folder_processing: bool = field(
        default_factory=lambda: _env("RECURSIVE_FOLDER_PROCESSING", True, bool)
    )

    # ---- 上下文 ----
    context_window: int = field(
        default_factory=lambda: _env("CONTEXT_WINDOW", 1, int)
    )
    context_mode: str = field(default_factory=lambda: _env("CONTEXT_MODE", "page", str))
    max_context_tokens: int = field(
        default_factory=lambda: _env("MAX_CONTEXT_TOKENS", 2000, int)
    )
    include_headers: bool = field(
        default_factory=lambda: _env("INCLUDE_HEADERS", True, bool)
    )
    include_captions: bool = field(
        default_factory=lambda: _env("INCLUDE_CAPTIONS", True, bool)
    )
    context_filter_content_types: List[str] = field(
        default_factory=lambda: [
            x.strip()
            for x in _env("CONTEXT_FILTER_CONTENT_TYPES", "text", str).split(",")
        ]
    )
    content_format: str = field(default_factory=lambda: _env("CONTENT_FORMAT", "minerU", str))

    # ---- 路径 ----
    use_full_path: bool = field(
        default_factory=lambda: _env("USE_FULL_PATH", False, bool)
    )

    # ---- LLM / Embedding / Reranker（QueryNest 新增检索配置）----
    llm_binding: str = field(default_factory=lambda: _env("LLM_BINDING", "openai", str))
    llm_base_url: str = field(
        default_factory=lambda: _env("LLM_BASE_URL", "https://api.openai.com/v1", str)
    )
    llm_api_key: str = field(default_factory=lambda: _env("LLM_API_KEY", "", str))
    llm_model: str = field(default_factory=lambda: _env("LLM_MODEL", "gpt-4o", str))
    llm_temperature: float = field(
        default_factory=lambda: _env("LLM_TEMPERATURE", 0.0, float)
    )
    llm_max_tokens: int = field(
        default_factory=lambda: _env("LLM_MAX_TOKENS", 8192, int)
    )

    embedding_binding: str = field(
        default_factory=lambda: _env("EMBEDDING_BINDING", "openai", str)
    )
    embedding_model: str = field(
        default_factory=lambda: _env("EMBEDDING_MODEL", "text-embedding-3-small", str)
    )
    embedding_binding_host: str = field(
        default_factory=lambda: _env("EMBEDDING_BINDING_HOST", "http://localhost:11434", str)
    )
    embedding_binding_api_key: str = field(
        default_factory=lambda: _env("EMBEDDING_BINDING_API_KEY", "", str)
    )
    embedding_dim: int = field(default_factory=lambda: _env("EMBEDDING_DIM", 1024, int))

    vision_model: str = field(default_factory=lambda: _env("VISION_MODEL", "gpt-4o", str))

    reranker_model: str = field(
        default_factory=lambda: _env("RERANKER_MODEL", "", str)
    )
    enable_rerank: bool = field(
        default_factory=lambda: _env("ENABLE_RERANK", False, bool)
    )

    # ---- 查询 ----
    query_top_k: int = field(default_factory=lambda: _env("QUERY_TOP_K", 20, int))

    # ---- API / 日志 ----
    api_host: str = field(default_factory=lambda: _env("API_HOST", "0.0.0.0", str))
    api_port: int = field(default_factory=lambda: _env("API_PORT", 9621, int))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO", str))

    # ---- 兼容：旧属性别名 ----
    def __post_init__(self):
        # 保持 working_dir / storage_dir 的兼容语义
        if self.storage_dir == "./querynest_storage" and self.working_dir != DEFAULT_WORKING_DIR:
            self.storage_dir = self.working_dir

    @property
    def mineru_parse_method(self) -> str:
        """向后兼容别名 -> parse_method。"""
        import warnings

        warnings.warn(
            "mineru_parse_method is deprecated. Use parse_method instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.parse_method

    @mineru_parse_method.setter
    def mineru_parse_method(self, value: str):
        import warnings

        warnings.warn(
            "mineru_parse_method is deprecated. Use parse_method instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.parse_method = value


def build_config(**overrides) -> QueryNestConfig:
    """从环境变量构建配置，并允许用关键字覆盖任意字段。"""
    cfg = QueryNestConfig()
    for key, value in overrides.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    return cfg