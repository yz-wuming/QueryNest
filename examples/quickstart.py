#!/usr/bin/env python
"""
QueryNest Quick Start

一个可运行的最小端到端演示：PDF/文本 → Parse → Index → Query → Citation。

用法（在项目根目录）::

    # 1) 先把你的文档（推荐 `examples/data/sample.txt` 或你自己的 PDF）交给脚本：
    python examples/quickstart.py examples/data/sample.txt

    # 或指定文档与问题：
    python examples/quickstart.py some.pdf "这个表格中哪个模型效果最好？"

运行前需要：
1. 在项目根创建 `.env`（可参考 `.env.example`），配置 OpenAI-compatible API：
     QUERYNEST_LLM_BASE_URL=...
     QUERYNEST_LLM_API_KEY=...
     QUERYNEST_LLM_MODEL=...
     QUERYNEST_EMBEDDING_MODEL=...
   （LLM 与 Embedding 可共用同一个供应商与 API Key）
2. 文档建议用轻量文本（`examples/data/sample.txt`），无需安装 MinerU；
   若用 PDF 需要安装 MinerU（重型）。

脚本输出:
    Document ingestion started
    Parsing completed
    Indexing completed
    Question: ...
    Answer: ...
    Sources:
    [1] xxx.txt — Page ...
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _load_dotenv() -> None:
    from querynest.core.clients import load_env

    # 优先当前目录 .env，其次项目根 .env
    load_env(str(_root_dotenv())) if _root_dotenv().exists() else load_env(str(_HERE / ".env"))


def _root_dotenv() -> Path:
    return _ROOT / ".env"


def _ensure_ready(cfg) -> None:
    if not cfg.llm_api_key:
        sys.exit(
            "[querynest] 未配置 LLM API Key。请在项目根 .env 中设置 QUERYNEST_LLM_API_KEY。\n"
            "参考 env.example。"
        )
    import querynest

    if not querynest.engine_available():
        sys.exit("[querynest] 引擎不可用：缺少 lightrag，请先 `pip install lightrag-hku`。")


async def run(
    document: str,
    question: str,
    *,
    parse_method: str = "auto",
) -> None:
    from querynest import QueryNest, QueryNestConfig
    from querynest.core.clients import (
        build_openai_embedding_func,
        build_openai_llm_func,
    )

    _load_dotenv()
    cfg = QueryNestConfig()
    _ensure_ready(cfg)

    # 轻量文本走 lite 解析，PDF 用配置的解析器
    doc = Path(document)
    if doc.suffix.lower() in (".txt", ".md"):
        cfg.parser = "lite"

    print("Document ingestion started")
    engine = QueryNest(
        cfg,
        llm_model_func=build_openai_llm_func(cfg),
        embedding_func=build_openai_embedding_func(cfg),
    )
    print(f"  -> {doc.name}")
    meta = await engine.ingest(document, parse_method=parse_method)
    print("Parsing completed")
    print("Indexing completed")
    print(f"  doc_id={meta.document_id}")

    print("\nQuestion:")
    print(f"  {question}")
    result = await engine.query(question, mode="mix", top_k=cfg.query_top_k)

    print("\nAnswer:")
    print(f"  {result.answer}")

    print("\nSources:")
    if result.sources:
        for i, src in enumerate(result.sources, 1):
            loc = src.page if src.page and str(src.page).lower() not in ("0", "none") else "n/a"
            print(f"  [{i}] {src.document_name or src.source or '?'} — {'Page ' + str(loc) if loc != 'n/a' else loc}")
    else:
        # 若无结构化来源，展示检索原始命中（尽力而为，绝不让 LLM 编 page）
        raw = result.retrieval or []
        for i, hit in enumerate(raw[:10], 1):
            print(f"  [{i}] {hit.get('source') or hit.get('document_id') or '?'} — {hit.get('content_type','text')}")

    if hasattr(engine, "close"):
        engine.close()
    print("\nDone.")


def main(argv=None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    document = args[0] if args else str(_HERE / "data" / "sample.txt")
    question = (
        args[1]
        if len(args) > 1
        else "QueryNest 的混合检索包含哪几条召回路径？它们如何融合？"
    )
    try:
        asyncio.run(run(document, question))
        return 0
    except FileNotFoundError as e:
        print(f"[querynest] 文档不存在: {e}")
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[querynest] 执行失败 ({type(e).__name__}): {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())