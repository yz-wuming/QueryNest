"""
QueryNest 命令行接口

用法示例::

    querynest ingest document.pdf
    querynest query "这个表格中哪个模型效果最好？"
    querynest documents list
    querynest documents delete <id>
    querynest evaluate datasets/evalset.json
    querynest serve

命令名统一为 ``querynest``，不再出现旧的 ``raganything``。
"""

import argparse
import asyncio
import json
import os
import sys
from typing import List, Optional

from querynest.core.exceptions import QueryNestError


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="querynest",
        description="QueryNest — Multimodal Document Intelligence & RAG",
    )
    p.add_argument("--version", action="store_true", help="show version and exit")
    sub = p.add_subparsers(dest="command", required=False)

    # ingest
    ingest = sub.add_parser("ingest", help="解析并入库一个文档")
    ingest.add_argument("path", help="文档路径或 URL")
    ingest.add_argument("--parse-method", default=None, help="解析方法: auto/ocr/txt")
    ingest.add_argument("--doc-id", default=None, help="显式文档 ID")

    # query
    q = sub.add_parser("query", help="发起一次查询")
    q.add_argument("question", nargs="?", help="问题文本")
    q.add_argument("--mode", default="mix", help="LightRAG 查询模式: local/global/hybrid/mix")
    q.add_argument("--top-k", type=int, default=20)
    q.add_argument("--rerank", action="store_true", help="启用重排")

    # documents
    docs = sub.add_parser("documents", help="知识库文档管理")
    dsub = docs.add_subparsers(dest="doc_action", required=True)
    dsub.add_parser("list", help="列出所有文档")
    dget = dsub.add_parser("get", help="获取单个文档")
    dget.add_argument("id")
    ddel = dsub.add_parser("delete", help="删除一个文档")
    ddel.add_argument("id")

    # evaluate
    ev = sub.add_parser("evaluate", help="运行 RAG 评测")
    ev.add_argument("dataset", help="评测数据集 JSON/JSONL 路径")
    ev.add_argument("--output", default="evaluation/results.json", help="结果输出路径")
    ev.add_argument("--top-k", type=int, default=10)

    # serve
    sub.add_parser("serve", help="启动 FastAPI 服务")

    return p


def _load_engine(args):
    """惰性构建引擎；依赖 lightrag，失败时给出清晰错误。"""
    from querynest import QueryNest, QueryNestConfig, engine_available

    if not engine_available():
        raise QueryNestError(
            "QueryNest 引擎不可用：缺少 lightrag 依赖。"
            "请先 `pip install lightrag`，或补全 LLM / Embedding 配置。"
        )
    cfg = QueryNestConfig()
    if args.command == "query" and args.rerank:
        cfg.enable_rerank = True
    return QueryNest(cfg, enable_rerank=cfg.enable_rerank)


def cmd_version() -> int:
    from querynest import __version__

    print(f"QueryNest {__version__} — Multimodal Document Intelligence & RAG")
    return 0


async def cmd_ingest(args) -> int:
    if not getattr(args, "doc_id", None):
        from querynest import engine_available

        if not engine_available():
            raise QueryNestError("无法初始化引擎（缺少 lightrag）。请先安装依赖。")
    engine = _load_engine(args)
    doc_id = getattr(args, "doc_id", None) or None
    meta = await engine.ingest(args.path, parse_method=args.parse_method, doc_id=doc_id)
    print(json.dumps(meta.to_dict(), ensure_ascii=False, indent=2))
    print(f"\n已入库: {meta.document_id} — {meta.filename}")
    return 0


async def cmd_query(args) -> int:
    from querynest import engine_available
    from querynest.core.exceptions import QueryError
    from querynest.core.models import RetrievalResult

    if not engine_available():
        raise QueryNestError("无法初始化引擎（缺少 lightrag）。请先安装依赖。")
    engine = _load_engine(args)

    question = (args.question or "").strip()
    if not question:
        raise QueryError("未提供问题，请用 `querynest query \"你的问题\"`")

    result = await engine.query(
        question, mode=args.mode, top_k=args.top_k
    )
    result: RetrievalResult
    out = {
        "answer": result.answer,
        "sources": [c.to_dict() for c in result.sources],
        "retrieval": result.retrieval,
        "metadata": result.metadata,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def cmd_documents(args) -> int:
    from querynest import DocumentStore, QueryNestConfig

    store = DocumentStore(storage_dir=str(QueryNestConfig().storage_dir))
    action = args.doc_action
    if action == "list":
        rows = store.list_documents()
        if not rows:
            print("知识库为空。")
            return 0
        for r in rows:
            print(
                f"  {r.get('document_id',''):<24} {r.get('filename',''):<32} "
                f"status={r.get('status','ready')} type={r.get('file_type','')}"
            )
        print(f"\n共 {len(rows)} 个文档")
    elif action == "get":
        row = store.get_document(args.id)
        print(json.dumps(row, ensure_ascii=False, indent=2))
    elif action == "delete":
        ok = store.delete_document(args.id)
        if ok:
            print(f"已删除文档: {args.id}")
        else:
            print(f"文档不存在: {args.id}")
            return 1
    return 0


def cmd_evaluate(args) -> int:
    from querynest.evaluation.runner import EvalRunner

    def _dummy_retriever(q):
        # 无引擎时可空评测；有数据集的 recall 计算将如实反映空检索
        return []

    runner = EvalRunner(
        retriever=_dummy_retriever,
        output_path=os.path.abspath(args.output),
    )
    report = runner.run(args.dataset, top_k=args.top_k)
    print(runner.summarize(report))
    print(f"\n结果已写入: {os.path.abspath(args.output)}")
    return 0


def cmd_serve(args) -> int:
    from querynest.api.server import serve

    # CLI 已在 asyncio.run 内部，而 uvicorn.run() 会再次调用 asyncio.run，
    # 因此在独立线程中启动服务器，避免“不能在运行中的事件循环内调用 asyncio.run”。
    import threading

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    t.join()
    return 0


async def _dispatch(args) -> int:
    if getattr(args, "version", False):
        return cmd_version()
    cmd = args.command
    if cmd == "ingest":
        return await cmd_ingest(args)
    if cmd == "query":
        return await cmd_query(args)
    if cmd == "documents":
        return cmd_documents(args)
    if cmd == "evaluate":
        return cmd_evaluate(args)
    if cmd == "serve":
        return cmd_serve(args)
    if cmd is None:
        _build_parser().print_help()
        return 0
    raise QueryNestError(f"未知命令: {cmd}")


def main(argv: Optional[List[str]] = None) -> int:
    # 确保从 .env 载入 QUERYNEST_*（幂等；.env 缺失时静默跳过）
    from querynest.core.clients import load_env

    load_env()
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except QueryNestError as e:
        print(f"[querynest] 错误 ({e.__class__.__name__}): {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n[querynest] 已中断", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())