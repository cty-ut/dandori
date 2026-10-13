"""错误体系（架构设计 7.2、语法设计第 4 节）

错误码一旦发布就是公开契约，不再改变含义。
所有错误信息集中在 MESSAGES 里，便于统一打磨措辞，也便于以后导出成文档。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from dandori.frontend.source import Source, Span

MESSAGES = {
    # E1xxx 字句解析
    "E1001": "「{word}」という言葉はわかりません",
    "E1002": "「」が閉じていません",
    "E1003": "『ー』は長音記号です。引き算には『-』か『－』を使ってください",
    # E2xxx 構文解析
    "E2001": "文の終わりに「。」をつけてください",
    "E2002": "「、」は「〜して」の後にだけ使えます",
    "E2003": "後ろに続きがあるときは、「{verb}」を「〜して」の形にしてください",
    "E2004": "文の最後は「{verb}」のように言い切りの形にしてください",
    "E2005": "「(」に対応する「)」がありません",
    "E2006": "何をするか（動詞）が書かれていません",
    "E2007": "「{word}」の後に「を」「に」などの助詞が必要です",
    "E2008": "ここに「{word}」は置けません",
}


@dataclass(frozen=True)
class Diagnostic:
    """一条错误：错误码 + 日语信息 + 原文位置"""

    code: str
    message: str
    span: Span

    @classmethod
    def make(cls, code: str, span: Span, **params: str) -> Diagnostic:
        return cls(code, MESSAGES[code].format(**params), span)


def _display_width(text: str) -> int:
    """终端里的显示宽度：全角字符占 2 格"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def render(diag: Diagnostic, source: Source) -> str:
    """把错误渲染成人能读的样子，用 ^ 指出原文中出错的位置

        エラー[E1001]: 「ねこ」という言葉はわかりません
         --> 例.dandori:1:5
          |
        1 | 100とねこを足す。
          |      ^^^^
    """
    line, col = source.line_col(diag.span.start)
    text = source.line_text(line)
    before = text[: col - 1]
    # 错误范围可能跨行，这里只标出第一行内的部分
    marked = text[col - 1 : col - 1 + max(diag.span.end - diag.span.start, 1)]
    gutter = " " * len(str(line))
    return "\n".join([
        f"エラー[{diag.code}]: {diag.message}",
        f"{gutter}--> {source.name}:{line}:{col}",
        f"{gutter} |",
        f"{line} | {text}",
        f"{gutter} | {' ' * _display_width(before)}{'^' * max(_display_width(marked), 1)}",
    ])
