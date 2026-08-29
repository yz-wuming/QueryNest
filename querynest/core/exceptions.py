"""
QueryNest 统一异常体系。

不吞异常：所有失败都抛出具名异常并携带错误上下文，便于上层（CLI / FastAPI / 测试）
精准判断与处理。
"""

from typing import Any, Dict, Optional


class QueryNestError(Exception):
    """QueryNest 基类异常。"""

    def __init__(self, message: str, *, context: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.context = context or {}
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "context": self.context,
        }


class ConfigurationError(QueryNestError):
    """配置错误：环境变量缺失、类型非法等。"""


class DocumentParseError(QueryNestError):
    """文档解析失败。"""


class DocumentNotFoundError(QueryNestError):
    """请求的文档在知识库中不存在。"""


class RetrievalError(QueryNestError):
    """检索失败（向量 / 关键字 / 图任一路由异常）。"""


class RerankError(QueryNestError):
    """重排（Rerank）失败。"""


class QueryError(QueryNestError):
    """生成阶段失败（LLM 调用、上下文构造等）。"""


class CitationError(QueryNestError):
    """引用来源加工 / 归一化失败。"""


class EvaluationError(QueryNestError):
    """评测运行失败（数据集格式、指标计算等）。"""