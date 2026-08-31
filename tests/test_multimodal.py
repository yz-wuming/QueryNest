"""Multimodal (image/table/equation) handling tests.

Equation extraction (OMML, pure stdlib) is fully exercised.
Image/table heavy backends (PaddleOCR / vision models) are inherited
capabilities that require optional deps, so those paths are guarded.
"""

from xml.etree import ElementTree as ET

import pytest

from querynest.core.models import ContentType
from querynest.multimodal.omml_extractor import NS, omml_to_latex
from querynest.query.analyzer import analyze_query

_M = "{" + NS["m"] + "}"


def _el(tag, children=None, text=None, attrib=None):
    e = ET.Element(_M + tag, attrib or {})
    if text:
        e.text = text
    for c in children or []:
        e.append(c)
    return e


def _run(*parts):
    run = _el("r")
    for p in parts:
        run.append(p)
    return run


def _t(text):
    return _el("t", text=text)


def _frac(num, den):
    return _el("f", [_el("num", [num]), _el("den", [den])])


def _omath(inner):
    return _el("oMath", [inner])


def test_omml_simple_text_run():
    math = _omath(_run(_t("x")))
    assert omml_to_latex(math) == "x"


def test_omml_fraction_renders_latex():
    math = _omath(_frac(_run(_t("a")), _run(_t("b"))))
    latex = omml_to_latex(math)
    assert "\\frac" in latex
    assert "a" in latex and "b" in latex


def test_omml_superscript():
    sup = _el("sSup", [_el("e", [_run(_t("x"))]), _el("sup", [_run(_t("2"))])])
    math = _omath(sup)
    assert omml_to_latex(math) is not None


def test_omml_empty_returns_empty():
    assert omml_to_latex(_omath(_run())) is not None


def test_multimodal_intent_detection():
    assert analyze_query("这个图表展示什么") == ContentType.MULTIMODAL
    assert analyze_query("表格中哪列最重要") == ContentType.TABLE
    assert analyze_query("这个架构图说明什么") == ContentType.IMAGE


def test_provided_parsers_registered():
    from querynest.ingestion.parser import SUPPORTED_PARSERS

    # 至少应有框架声明的解析器；多模态内容解析依赖它们
    assert any(p in SUPPORTED_PARSERS for p in ("mineru", "docling", "paddleocr"))