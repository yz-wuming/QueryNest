"""
Lite Text Parser — QueryNest 轻量文本解析器

在未安装 MinerU / Docling / PaddleOCR 等重型解析依赖的情况下，直接读取
``.txt`` / ``.md`` 文本文件，将内容按空行分段为标准的 ``content_list``
文本块，从而让「纯文本文档 → 索引 → 检索 → 回答 → 引用」的轻量端到端
流程在零重型依赖下即可运行。

这是 **QueryNest 新增**的解析器（不来自 RAG-Anything），通过 parser 注册表
以 ``"lite"`` 名称接入既有流水线，不修改 MinerU / Docling / PaddleOCR 的
原始实现。

用法::

    from querynest.ingestion.parser import get_parser
    parser = get_parser("lite")
    content_list, = parser.parse_document("notes.txt")

或通过配置选择:

    QUERYNEST_PARSER=lite
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Union

# 延迟 import 以避免循环：本模块被 parser.get_parser 在运行时才引入
from querynest.ingestion.parser import Parser


class LiteTextParser(Parser):
    """纯文本直读解析器，仅处理 ``.txt`` / ``.md``，不依赖任何重型解析库。"""

    def check_installation(self) -> bool:
        # 纯 stdlib 实现，始终可用
        return True

    def parse_document(
        self,
        file_path: Union[str, Path],
        method: str = "auto",
        output_dir: Union[str, Path, None] = None,
        lang: Union[str, None] = None,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        del method, output_dir, lang  # 本解析器不需要这些参数
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        ext = path.suffix.lower()
        if ext not in self.TEXT_FORMATS:
            from querynest.core.exceptions import DocumentParseError

            raise DocumentParseError(
                f"LiteTextParser 仅支持 {sorted(self.TEXT_FORMATS)}，收到 '{ext}'",
                context={"file": str(path)},
            )

        text = self._read_text(path)
        chunks = self._split_paragraphs(text)
        return [
            {"type": "text", "text": chunk, "text_format": "plain", "page_idx": 0}
            for chunk in chunks
        ]

    # ------------------------------------------------------------ #
    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            for enc in ("gbk", "gb18030", "latin-1"):
                try:
                    return path.read_text(encoding=enc)
                except UnicodeDecodeError:
                    continue
            return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def _split_paragraphs(text: str, max_chars: int = 1200) -> List[str]:
        text = text.strip()
        if not text:
            return []
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        chunks: List[str] = []
        buf = ""
        for para in paras:
            if len(buf) + len(para) + 1 <= max_chars:
                buf = (buf + "\n\n" + para) if buf else para
            else:
                if buf:
                    chunks.append(buf)
                # 超长段落按句子/字符再切
                if len(para) > max_chars:
                    for i in range(0, len(para), max_chars):
                        chunks.append(para[i : i + max_chars])
                else:
                    buf = para
        if buf:
            chunks.append(buf)
        return chunks