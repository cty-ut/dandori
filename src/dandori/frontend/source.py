"""源码与位置

所有 Token、AST 节点、错误信息都用 Span 指回用户写的原文（架构设计 5.6）。
位置用"字符下标"记录，需要显示时再换算成"第几行第几列"。
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    """原文中的一段范围：[start, end)，单位是字符下标"""

    start: int
    end: int


class Source:
    """一份源码：文件名 + 原文"""

    def __init__(self, text: str, name: str = "<入力>"):
        self.text = text
        self.name = name
        # 每一行开头的字符下标，用来把下标换算成行号
        self._line_starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]

    def line_col(self, offset: int) -> tuple[int, int]:
        """字符下标 → (行, 列)，都从 1 开始"""
        line = bisect.bisect_right(self._line_starts, offset)
        col = offset - self._line_starts[line - 1] + 1
        return line, col

    def line_text(self, line: int) -> str:
        """第 line 行的内容（不含换行符）"""
        start = self._line_starts[line - 1]
        end = self.text.find("\n", start)
        text = self.text[start:] if end == -1 else self.text[start:end]
        return text.rstrip("\r")

    def slice(self, span: Span) -> str:
        return self.text[span.start:span.end]
