"""字句解析：把一串字符切成 Token（语法设计第 1 节）

核心思路只有一个：用一根"指针"（pos）从左往右扫，每到一个位置，按顺序尝试：
    1. 空白 → 跳过
    2. 数字 → 一直读到不是数字为止
    3. 「   → 一直读到 」为止
    4. 记号 → + - * / ( ) 、 。
    5. 查词典，取最长匹配 → 助词、动词、指示语
    6. 都不是 → 记一条错误，跳过这段不认识的内容，继续往后扫

遇到错误不立刻停下，而是记下来接着扫，这样一次就能报出所有错误。
"""

from __future__ import annotations

from decimal import Decimal

from dandori.errors import Diagnostic
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.normalize import normalize
from dandori.frontend.source import Source, Span
from dandori.frontend.tokens import Token, TokenKind

_WHITESPACE = set(" \t\r\n　")
_DIGITS = set("0123456789")
_SYMBOLS = {
    "+": TokenKind.OPERATOR,
    "-": TokenKind.OPERATOR,
    "*": TokenKind.OPERATOR,
    "/": TokenKind.OPERATOR,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "、": TokenKind.COMMA,
    "。": TokenKind.PERIOD,
}


def tokenize(source: Source, lexicon: Lexicon) -> tuple[list[Token], list[Diagnostic]]:
    return _Lexer(source, lexicon).run()


class _Lexer:
    def __init__(self, source: Source, lexicon: Lexicon):
        self.source = source
        self.lexicon = lexicon
        self.text = normalize(source.text)   # 扫描正規化后的文本
        self.pos = 0
        self.tokens: list[Token] = []
        self.errors: list[Diagnostic] = []

    def run(self) -> tuple[list[Token], list[Diagnostic]]:
        while self.pos < len(self.text):
            ch = self.text[self.pos]

            if ch in _WHITESPACE:
                self.pos += 1
            elif ch in _DIGITS:
                self._number()
            elif ch == "「":
                self._string()
            elif ch in _SYMBOLS:
                self._emit(_SYMBOLS[ch], self.pos, self.pos + 1, value=ch)
            elif ch == "ー" and self._between_numbers():
                self.errors.append(Diagnostic.make("E1003", Span(self.pos, self.pos + 1)))
                self.pos += 1
            elif match := self.lexicon.longest_match(self.text, self.pos):
                surface, entry = match
                self._emit(entry.kind, self.pos, self.pos + len(surface),
                           verb_id=entry.verb_id, lemma=entry.lemma, form=entry.form)
            else:
                self._unknown()

        end = len(self.text)
        self.tokens.append(Token(TokenKind.EOF, "", Span(end, end)))
        return self.tokens, self.errors

    # ---------- 各种 Token ----------

    def _number(self) -> None:
        """整数或小数：100、3.14

        小数用 Decimal 而不是 float：业务语言处理的是钱，
        float 的 0.1 + 0.2 = 0.30000000000000004，这在会计上不能接受。
        """
        start = self.pos
        while self._peek() in _DIGITS:
            self.pos += 1
        if self._peek() == "." and self._peek(1) in _DIGITS:
            self.pos += 1
            while self._peek() in _DIGITS:
                self.pos += 1

        digits = self.text[start:self.pos]
        value = Decimal(digits) if "." in digits else int(digits)
        self._emit(TokenKind.NUMBER, start, self.pos, value=value, advance=False)

    def _string(self) -> None:
        """「」中间的内容。不能跨行，没有 」 时报错，但仍然产生 Token 以便后续继续检查"""
        start = self.pos
        self.pos += 1
        while self.pos < len(self.text) and self.text[self.pos] not in "」\n":
            self.pos += 1

        content = self.source.text[start + 1:self.pos]   # 内容取原文，不经过正規化
        if self._peek() == "」":
            self.pos += 1
        else:
            self.errors.append(Diagnostic.make("E1002", Span(start, self.pos)))
        self._emit(TokenKind.STRING, start, self.pos, value=content, advance=False)

    def _unknown(self) -> None:
        """不认识的内容：一直跳到下一个认识的东西为止，整段报一条错误

        这样「ねこを」会报「ねこ」不认识，而不是「ね」「こ」各报一次。
        """
        start = self.pos
        self.pos += 1
        while self.pos < len(self.text) and not self._recognizable_at(self.pos):
            self.pos += 1
        word = self.source.text[start:self.pos]
        self.errors.append(Diagnostic.make("E1001", Span(start, self.pos), word=word))

    # ---------- 小工具 ----------

    def _emit(self, kind: TokenKind, start: int, end: int, *, advance: bool = True, **fields) -> None:
        span = Span(start, end)
        self.tokens.append(Token(kind, self.source.slice(span), span, **fields))
        if advance:
            self.pos = end

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.text[i] if i < len(self.text) else ""

    def _recognizable_at(self, i: int) -> bool:
        ch = self.text[i]
        return (ch in _WHITESPACE or ch in _DIGITS or ch in _SYMBOLS or ch == "「"
                or self.lexicon.matches_at(self.text, i))

    def _between_numbers(self) -> bool:
        """当前的「ー」是否夹在两个数字之间（忽略空白）：100ー50"""
        before = self.text[:self.pos].rstrip(" \t　")
        after = self.text[self.pos + 1:].lstrip(" \t　")
        return before[-1:] in _DIGITS and after[:1] in _DIGITS
