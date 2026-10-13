"""词典（语法设计 1.3、1.4、5.7）

日语没有空格，断词要靠"查词典"：在当前位置找能匹配上的最长的词。
词典的内容有三个来源：
- 语言核心的固定词：助词、指示语、キーワード
- 注册表里的动词：由辞书形 + 活用类型自动生成全部活用形
- 程序开头的用語声明：带平假名的名字（売り上げ、お客様）
"""

from __future__ import annotations

from dataclasses import dataclass

from dandori.frontend.conjugation import Form, conjugate
from dandori.frontend.tokens import TokenKind
from dandori.plugin.registry import Registry

PARTICLES = ("と", "を", "から", "で", "に", "は", "が", "まで", "の", "ごとに")
PRONOUNS = ("それ", "その")   # その ＝ それの
# 最长匹配保证「合計とする」切成 合計 / とする，而不是 合計 / と / する；
# 「でない」「を超える」同理，不会被切成助词「で」「を」
KEYWORDS = (
    "とする",
    "もし", "ならば", "そうでなく", "そうでなければ", "ここまで",
    "以上", "以下", "未満", "を超える", "でない",
    "かつ", "または",
    # 「回」不单独作为关键字，否则词典优先规则会把「回数」「回収」切坏
    "回繰り返して", "繰り返して",
    # 表格操作（语法设计第 9 节）。「のもの」「もの」都认：名词后接「の」，动词、形容词后直接接
    "のもの", "もの", "空欄", "大きい順", "小さい順",
    # 逐行处理（语法设计第 10 节）。整体作为关键字，「次回」「繰り返し回数」等名字不受影响
    "のそれぞれについて", "繰り返しをやめる", "次に進む",
)


@dataclass(frozen=True)
class Entry:
    kind: TokenKind
    verb_id: str | None = None
    lemma: str | None = None
    form: Form | None = None


class Lexicon:
    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}
        self._max_len = 0

    def add(self, surface: str, entry: Entry) -> None:
        existing = self._entries.get(surface)
        if existing is not None and existing != entry:
            raise ValueError(f"辞書の衝突：「{surface}」が二通りに登録されています")
        self._entries[surface] = entry
        self._max_len = max(self._max_len, len(surface))

    def longest_match(self, text: str, pos: int) -> tuple[str, Entry] | None:
        """从 text[pos] 开始，找能匹配上的最长的词

        例：词典里同时有「と」和「とる」时，遇到「とる」要匹配「とる」，而不是「と」。
        做法很朴素：从最长的长度开始试，逐个缩短。
        """
        for length in range(min(self._max_len, len(text) - pos), 0, -1):
            surface = text[pos:pos + length]
            entry = self._entries.get(surface)
            if entry is not None:
                return surface, entry
        return None

    def matches_at(self, text: str, pos: int) -> bool:
        return self.longest_match(text, pos) is not None

    def get(self, surface: str) -> Entry | None:
        return self._entries.get(surface)

    def copy(self) -> Lexicon:
        """每个程序在副本上添加自己的用語，不影响共享的词典"""
        new = Lexicon()
        new._entries = dict(self._entries)
        new._max_len = self._max_len
        return new

    @classmethod
    def from_registry(cls, registry: Registry) -> Lexicon:
        lexicon = cls()
        for word in PARTICLES:
            lexicon.add(word, Entry(TokenKind.PARTICLE))
        for word in PRONOUNS:
            lexicon.add(word, Entry(TokenKind.PRONOUN))
        for word in KEYWORDS:
            lexicon.add(word, Entry(TokenKind.KEYWORD))
        for verb in registry.verbs:
            for form, surface in conjugate(verb.dictionary_form, verb.conjugation).items():
                lexicon.add(surface, Entry(TokenKind.VERB, verb.id, verb.dictionary_form, form))
        return lexicon
