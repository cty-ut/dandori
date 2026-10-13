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
    "E1004": "「{word}」：名前にひらがなは使えません。{hint}",
    "E1005": "「{word}」は用語にできません。漢字・カタカナ・ひらがなで書いてください",
    "E1006": "「{word}」はすでに別の言葉として使われているので、用語にできません",
    "E1007": "「用語：」はプログラムの最初に書いてください",
    "E1008": "「用語：」の終わりに「。」をつけてください",
    "E1009": "「なら」ではなく「ならば」と書いてください",
    # E2xxx 構文解析
    "E2001": "文の終わりに「。」をつけてください",
    "E2002": "「、」は「〜して」の後にだけ使えます",
    "E2003": "後ろに続きがあるときは、「{verb}」を「〜して」の形にしてください",
    "E2004": "文の最後は「{verb}」のように言い切りの形にしてください",
    "E2005": "「(」に対応する「)」がありません",
    "E2006": "何をするか（動詞）が書かれていません",
    "E2007": "「{word}」の後に「を」「に」などの助詞が必要です",
    "E2008": "ここに「{word}」は置けません",
    "E2009": "「とする」は「合計は〜とする」か「〜して、合計とする」の形で使います",
    "E2010": "「{name}は〜」の後に「とする」が必要です",
    "E2011": "条件の後に「ならば」が必要です",
    "E2012": "「もし」に対応する「ここまで。」がありません",
    "E2013": "「{word}」に対応する「もし」がありません",
    "E2014": "「かつ」と「または」を一つの条件に混ぜることはできません。「もし」を二つに分けてください",
    "E2015": "条件の書き方がわかりません（例：「合計が100以上」「合計>=100」）",
    # E3xxx 意味解析
    "E3001": "「{verb}」には「{particle}」は使えません",
    "E3002": "「{particle}」が二回使われています",
    "E3003": "「{verb}」には「〜{particle}」が必要です",
    "E3004": "前の結果をどこに入れるかわかりません。「それ」を使って書いてください",
    "E3005": "前の手順の結果が使われていません",
    "E3006": "「それ」が指すものがありません",
    "E3007": "「{verb}」の「〜{particle}」には{expected}が必要です（{actual}）",
    "E3008": "計算には数が必要です（{actual}）",
    "E3009": "「{name}」は{old}なので、{new}を入れることはできません",
    "E3010": "「{name}」はまだ決まっていません",
    "E3011": "「{verb}」には結果がないので、名前をつけられません",
    "E3012": "「{op}」で比べられるのは数だけです（{actual}）",
    "E3013": "{left}と{right}は比べられません",
    "E3014": "「{name}」は決まらない場合があります。すべての場合で決めてください（「そうでなければ」も含めて）",
    # E5xxx 実行
    "E5001": "0で割ることはできません",
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
