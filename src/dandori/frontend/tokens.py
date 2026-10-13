"""Token：断词的结果（语法设计 1.1、1.2）"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from dandori.frontend.conjugation import Form
from dandori.frontend.source import Span


class TokenKind(Enum):
    NUMBER = "数"
    STRING = "文字列"
    PARTICLE = "助詞"
    VERB = "動詞"
    PRONOUN = "指示語"
    NAME = "名前"
    KEYWORD = "キーワード"
    OPERATOR = "演算子"
    COMPARE = "比較記号"
    LPAREN = "左括弧"
    RPAREN = "右括弧"
    COMMA = "読点"
    PERIOD = "句点"
    UNKNOWN = "不明"      # 不认识的词：已经报过 E1001，留作占位，避免后续阶段连锁报错
    EOF = "終端"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str              # 用户写的原文（正規化之前），如「×」「１００」
    span: Span
    # 以下字段只有部分种类才有
    value: object = None    # 数或金額的值、字符串的内容、演算子的标准写法（+-*/）、名字（正規化后）
    verb_id: str | None = None                # 动词的完整标识，如 "std.足す"
    lemma: str | None = None                  # 动词的辞书形
    form: Form | None = None                  # 动词的活用形

    def describe(self) -> str:
        """给人看的简短说明，调试用"""
        if self.kind is TokenKind.VERB:
            return f"{self.kind.value} {self.text}（{self.lemma}・{self.form.value}）"
        if self.kind is TokenKind.STRING:
            return f"{self.kind.value} 「{self.value}」"
        if self.kind is TokenKind.EOF:
            return self.kind.value
        return f"{self.kind.value} {self.text}"
