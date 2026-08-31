"""
QueryNest 统一日志

所有 QueryNest 模块统一通过 ``get_logger`` 获取 logger，日志名称统一以
``querynest`` 开头。本模块无第三方依赖。
"""

import logging

# 统一日志名称根
_LOGGER_ROOT = "querynest"


def get_logger(name: str) -> logging.Logger:
    """返回 QueryNest 命名空间下的 logger。

    Args:
        name: 子模块名，例如 ``"core.engine"``。内部会自动拼接 ``querynest.`` 前缀。

    Returns:
        logging.Logger
    """
    if name == _LOGGER_ROOT or name.startswith(_LOGGER_ROOT + "."):
        qualified = name
    else:
        qualified = f"{_LOGGER_ROOT}.{name}"
    return logging.getLogger(qualified)


def basic_config(level: int = logging.INFO, fmt: str = None) -> None:
    """为 QueryNest 配置根日志格式，便于 CLI / API 复用。"""
    if fmt is None:
        fmt = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    logging.basicConfig(level=level, format=fmt)