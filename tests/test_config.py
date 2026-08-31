"""Configuration system tests (pure Python, no third-party deps)."""

import os

from querynest.core.config import QueryNestConfig, build_config


def test_default_config_fields():
    cfg = QueryNestConfig()
    assert cfg.parser in ("mineru", "docling", "paddleocr")
    assert cfg.parse_method == "auto"
    assert cfg.query_top_k > 0
    assert isinstance(cfg.enable_image_processing, bool)
    assert isinstance(cfg.enable_table_processing, bool)
    assert isinstance(cfg.enable_equation_processing, bool)
    assert isinstance(cfg.enable_rerank, bool)
    assert cfg.storage_dir


def test_env_override_with_querynest_prefix(monkeypatch, tmp_path):
    monkeypatch.setenv("QUERYNEST_PARSER", "docling")
    monkeypatch.setenv("QUERYNEST_QUERY_TOP_K", "42")
    monkeypatch.setenv("QUERYNEST_ENABLE_RERANK", "true")
    cfg = QueryNestConfig()
    assert cfg.parser == "docling"
    assert cfg.query_top_k == 42
    assert cfg.enable_rerank is True


def test_env_legacy_fallback(monkeypatch):
    # 未设置 QUERYNEST_ 时回退旧变量名
    monkeypatch.delenv("QUERYNEST_PARSER", raising=False)
    monkeypatch.setenv("PARSER", "paddleocr")
    cfg = QueryNestConfig()
    assert cfg.parser == "paddleocr"


def test_build_config_overrides():
    cfg = build_config(parser="txt", query_top_k=7)
    assert cfg.parser == "txt"
    assert cfg.query_top_k == 7


def test_deprecated_alias_warns():
    import warnings

    cfg = QueryNestConfig()
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        assert cfg.mineru_parse_method == cfg.parse_method
    assert any(issubclass(x.category, DeprecationWarning) for x in w)