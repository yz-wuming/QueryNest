"""
QueryNest 迁移脚本（一次性工具，运行后即完成包迁移）。

将 raganything/ 迁移并重构为分层结构 querynest/：
  - 移动文件到目标子包
  - 重写所有 import（raganything.X -> querynest.<新路径>）
  - 重命名公共 API（RAGAnythingConfig -> QueryNestConfig；RAGAnything -> QueryNest）
  - 重命名日志/临时前缀/包字符串

只处理纯文本替换，不触碰第三方依赖代码。
"""
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "raganything"
DST = ROOT / "querynest"

# 旧模块(相对包) -> 新模块(相对包) 映射
MOVE = {
    "__init__.py": "__init__.py",
    "base.py": "base.py",
    "utils.py": "utils.py",
    "asset_urls.py": "asset_urls.py",
    "prompt.py": "prompt.py",
    "prompts_zh.py": "prompts_zh.py",
    "prompt_manager.py": "prompt_manager.py",
    "callbacks.py": "callbacks.py",
    "resilience.py": "resilience.py",
    "raganything.py": "core/engine.py",
    "config.py": "core/config.py",
    "processor.py": "ingestion/processor.py",
    "parser.py": "ingestion/parser.py",
    "batch.py": "ingestion/batch.py",
    "batch_parser.py": "ingestion/batch_parser.py",
    "modalprocessors.py": "multimodal/processors.py",
    "omml_extractor.py": "multimodal/omml_extractor.py",
    "enhanced_markdown.py": "multimodal/enhanced_markdown.py",
    "query.py": "query/base.py",
}

# 包节点级 import 重写（在 raganything.->querynest. 之后按序应用）
# 注意顺序：更长/更特化的在前
MODULE_IMPORTS = [
    ("querynest.raganything", "querynest.core.engine"),
    ("querynest.batch_parser", "querynest.ingestion.batch_parser"),
    ("querynest.modalprocessors", "querynest.multimodal.processors"),
    ("querynest.enhanced_markdown", "querynest.multimodal.enhanced_markdown"),
    ("querynest.omml_extractor", "querynest.multimodal.omml_extractor"),
    ("querynest.processor", "querynest.ingestion.processor"),
    ("querynest.parser", "querynest.ingestion.parser"),
    ("querynest.config", "querynest.core.config"),
    ("querynest.batch", "querynest.ingestion.batch"),
    ("querynest.query", "querynest.query.base"),
]

# 类名/字符串重写（顺序：长的先替换，避免子串误伤）
PREFIX_RENAMES = [
    ("RAGAnythingConfig", "QueryNestConfig"),
    ("RAGAnything", "QueryNest"),
]
GENERIC_STRINGS = [
    ("raganything", "querynest"),
]


def _apply(text: str) -> str:
    # 1) 包名 token：raganything. -> querynest. ；裸 import raganything -> import querynest
    text = re.sub(r"\bimport raganything\b", "import querynest", text)
    text = re.sub(r"raganything\.", "querynest.", text)
    # 2) 模块级转写（含 .querynest. 已产生的情况）
    for old, new in MODULE_IMPORTS:
        text = text.replace(old, new)
    # 3) 类名/公共命名
    for old, new in PREFIX_RENAMES:
        text = re.sub(rf"\b{re.escape(old)}\b", new, text)
    # 4) 其余普通字符串（临时目录前缀、manifest 名、docstring）
    for old, new in GENERIC_STRINGS:
        text = text.replace(old, new)
    return text


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit(f"src package not found: {SRC}")
    if DST.exists():
        raise SystemExit(f"dst package already exists, refusing to overwrite: {DST}")

    # 创建目标包结构并写入改写后的内容
    for old_rel, new_rel in MOVE.items():
        src_file = SRC / old_rel
        dst_file = DST / new_rel
        if not src_file.is_file():
            print(f"[skip] missing {src_file}")
            continue
        dst_file.parent.mkdir(parents=True, exist_ok=True)
        text = src_file.read_text(encoding="utf-8")
        rewritten = _apply(text)
        dst_file.write_text(rewritten, encoding="utf-8")
        print(f"[moved+rewritten] {old_rel} -> {new_rel}")
        if rewritten != text:
            print(f"    ^ changed {count_diff(text, rewritten)} occurrences")

    print("\nDone. Old package left intact at raganything/ (will be removed after verification).")


def count_diff(old: str, new: str) -> int:
    return len(old) != len(new)


if __name__ == "__main__":
    main()