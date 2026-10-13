"""字句解析：把一串字符切成 Token（语法设计第 1 节）

核心思路只有一个：用一根"指针"（pos）从左往右扫，每到一个位置，按顺序尝试：
    1. 空白 → 跳过
    2. 数字 → 一直读到不是数字为止
    3. 「   → 一直读到 」为止
    4. 记号 → + - * / ( ) 、 。 以及比较符号 >= <= < > = <> != ≧ ≦ ≠
    5. 查词典，取最长匹配 → 助词、动词、指示语、キーワード、声明过的用語
    6. 汉字、片假名、英文字母开头 → 名字（语法设计 5.3）
    7. 都不是 → 记一条错误，留下"不明"占位 Token，继续往后扫

在这之前，先读程序开头的用語声明（语法设计 5.7）：
    用語：売り上げ、お客様。
声明的词被加进这个程序专用的词典，之后就能像其他词一样被认出来。

遇到错误不立刻停下，而是记下来接着扫，这样一次就能报出所有错误。
"""

from __future__ import annotations

from decimal import Decimal

from dandori.errors import Diagnostic
from dandori.frontend.lexicon import Entry, Lexicon
from dandori.frontend.normalize import normalize
from dandori.frontend.source import Source, Span
from dandori.frontend.tokens import Token, TokenKind

_WHITESPACE = set(" \t\r\n　")
_DIGITS = set("0123456789")
# 比较符号：先试两个字符的，再试一个字符的。值是标准写法
_COMPARE = {
    ">=": ">=", "<=": "<=", "<>": "<>", "!=": "<>",
    ">": ">", "<": "<", "=": "=",
    "≧": ">=", "≦": "<=", "≠": "<>",
}
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
        self.lexicon = lexicon.copy()        # 用語声明只加进这个程序自己的词典
        self.text = normalize(source.text)   # 扫描正規化后的文本
        self.pos = 0
        self.tokens: list[Token] = []
        self.errors: list[Diagnostic] = []

    def run(self) -> tuple[list[Token], list[Diagnostic]]:
        self._declarations()

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
            elif (op := self.text[self.pos:self.pos + 2]) in _COMPARE or (op := ch) in _COMPARE:
                self._emit(TokenKind.COMPARE, self.pos, self.pos + len(op), value=_COMPARE[op])
            elif ch == "ー" and self._between_numbers():
                self.errors.append(Diagnostic.make("E1003", Span(self.pos, self.pos + 1)))
                self.pos += 1
            elif self._at_declaration():
                # 用語声明只能写在开头：断词器必须在用到之前就认识这些词
                self.errors.append(Diagnostic.make("E1007", Span(self.pos, self.pos + 2)))
                self._read_declaration(register=False)
            elif match := self.lexicon.longest_match(self.text, self.pos):
                surface, entry = match
                if entry.kind is TokenKind.NAME:
                    self._name()             # 声明过的用語：可能还和别的名字连在一起
                else:
                    self._emit(entry.kind, self.pos, self.pos + len(surface),
                               verb_id=entry.verb_id, lemma=entry.lemma, form=entry.form)
            elif _is_name_start(ch):
                self._name()
            else:
                self._unknown(self.pos)

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

    def _name(self) -> None:
        """名字：连续的汉字、片假名、英文字母、数字（语法设计 5.3）

        两条规则：
        - 遇到平假名就结束：平假名留给助词和送假名
        - 词典优先：名字中间某处能匹配上词典里的词，名字就在那里结束，
          所以「合計表示する」会切成「合計」「表示する」

        声明过的用語可以出现在名字的任何位置：「売り上げ合計」「合計売り上げ」都是一个名字。
        """
        start = self.pos
        while self.pos < len(self.text):
            match = self.lexicon.longest_match(self.text, self.pos)
            if match and match[1].kind is TokenKind.NAME:
                self.pos += len(match[0])    # 整个用語都算名字的一部分
            elif match and self.pos > start:
                break                        # 词典优先
            elif _is_name_char(self.text[self.pos]):
                self.pos += 1
            else:
                break

        # 名字后面紧跟着平假名，而且那里不是助词等词典里的词：名字里混进了平假名
        if _is_hiragana(self._peek()) and not self.lexicon.matches_at(self.text, self.pos):
            self._unknown(start)
            return

        self._emit(TokenKind.NAME, start, self.pos, value=self.text[start:self.pos], advance=False)

    def _unknown(self, start: int) -> None:
        """不认识的内容：一直跳到下一个认识的东西为止，整段报一条错误

        这样「ねこを」会报「ねこ」不认识，而不是「ね」「こ」各报一次。
        平假名和汉字、片假名混在一起的（「売り上げ」「お客様」），多半是想写名字，给出专门的提示。
        """
        self.pos = max(self.pos, start + 1)
        while self.pos < len(self.text) and not self._recognizable_at(self.pos):
            self.pos += 1
        word = self.source.text[start:self.pos]
        span = Span(start, self.pos)
        looks_like_name = (any(_is_name_char(c) for c in word) and any(_is_hiragana(c) for c in word)
                           and not _looks_like_verb(word))
        if word.startswith("なら"):
            # 「合計が0なら、」：只认「ならば」（语法设计 6.4）
            self.errors.append(Diagnostic.make("E1009", span))
        elif "繰り返" in word:
            # 「3回繰り返す。」：只认「繰り返して、」（语法设计 7.3）
            self.errors.append(Diagnostic.make("E1010", span))
        elif looks_like_name:
            self.errors.append(Diagnostic.make("E1004", span, word=word, hint=_name_hint(word)))
        else:
            self.errors.append(Diagnostic.make("E1001", span, word=word))
        # 留下占位 Token，让構文解析知道"这里有个词"，而不是凭空消失导致连锁报错
        self._emit(TokenKind.UNKNOWN, start, self.pos, advance=False)

    # ---------- 用語声明 ----------

    def _declarations(self) -> None:
        """程序开头的用語声明，可以有多行"""
        while True:
            while self._peek() in _WHITESPACE:
                self.pos += 1
            if not self._at_declaration():
                return
            self._read_declaration(register=True)

    def _at_declaration(self) -> bool:
        return self.text.startswith("用語", self.pos) and self._peek(2) in (":", "：")

    def _read_declaration(self, register: bool) -> None:
        """用語：売り上げ、お客様。"""
        start = self.pos
        self.pos += 3                        # 用語：
        word_start = self.pos
        words = []
        while self.pos < len(self.text) and self.text[self.pos] != "。":
            if self.text[self.pos] == "、":
                words.append((word_start, self.pos))
                word_start = self.pos + 1
            self.pos += 1
        words.append((word_start, self.pos))

        if self.pos < len(self.text):
            self.pos += 1                    # 。
        else:
            self.errors.append(Diagnostic.make("E1008", Span(start, start + 3)))

        if register:
            for word_start, word_end in words:
                self._declare(word_start, word_end)

    def _declare(self, start: int, end: int) -> None:
        # 去掉词前后的空白和换行
        while start < end and self.text[start] in _WHITESPACE:
            start += 1
        while end > start and self.text[end - 1] in _WHITESPACE:
            end -= 1
        word = self.text[start:end]
        if word == "":
            return                           # 「答え、、お客様」「答え、。」：多打的「、」不影响意思
        span = Span(start, end)

        if not _is_valid_term(word):
            self.errors.append(Diagnostic.make("E1005", span, word=self.source.text[start:end]))
            return
        existing = self.lexicon.get(word)
        if existing is None:
            self.lexicon.add(word, Entry(TokenKind.NAME))
        elif existing.kind is not TokenKind.NAME:   # 和助词、动词等重名；重复声明则无妨
            self.errors.append(Diagnostic.make("E1006", span, word=word))

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
        return (ch in _WHITESPACE or ch in _DIGITS or ch in _SYMBOLS or ch in _COMPARE or ch == "「"
                or self.lexicon.matches_at(self.text, i))

    def _between_numbers(self) -> bool:
        """当前的「ー」是否夹在两个数字之间（忽略空白）：100ー50"""
        before = self.text[:self.pos].rstrip(" \t　")
        after = self.text[self.pos + 1:].lstrip(" \t　")
        return before[-1:] in _DIGITS and after[:1] in _DIGITS


# ---------- 字符种类 ----------

def _is_kanji(ch: str) -> bool:
    return ("一" <= ch <= "鿿"      # CJK 统一汉字
            or "㐀" <= ch <= "䶿"   # 扩展 A
            or "豈" <= ch <= "﫿"   # 兼容汉字
            or ch in "々〆")


def _is_katakana(ch: str) -> bool:
    return "ァ" <= ch <= "ヺ" or ch in "ヽヾ"


def _is_hiragana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゟ"


def _is_name_start(ch: str) -> bool:
    return _is_kanji(ch) or _is_katakana(ch) or (ch.isascii() and ch.isalpha())


def _is_name_char(ch: str) -> bool:
    return _is_name_start(ch) or ch == "ー" or ch in _DIGITS or ch == "_"


def _name_hint(word: str) -> str:
    """名字里有平假名时的修改建议

    以汉字或片假名开头的，去掉平假名往往就是公用文的标准写法：
    売り上げ → 売上、支払い → 支払、振り込み → 振込
    以平假名开头的（お客様）去掉后意思会变，只给一般性的提示。
    """
    declare = f"最初に「用語：{word}。」と書いてください"
    if _is_name_start(word[0]):
        return f"「{''.join(c for c in word if not _is_hiragana(c))}」のように書くか、{declare}"
    return f"漢字かカタカナで書くか、{declare}"



def _looks_like_verb(word: str) -> bool:
    """以う段假名或「て・で」结尾的，多半是写错的动词（「計算する」「振り込む」），而不是名字"""
    return word[-1] in "うくぐすつぬぶむるてで"


def _is_valid_term(word: str) -> bool:
    """用語：汉字、片假名、平假名、字母、数字组成，不能以数字等开头"""
    return (word != ""
            and (_is_name_start(word[0]) or _is_hiragana(word[0]))
            and all(_is_name_char(c) or _is_hiragana(c) for c in word))
