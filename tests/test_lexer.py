from decimal import Decimal

import pytest

from dandori.frontend.conjugation import Conjugation, Form
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.plugin.registry import Registry, VerbSpec, default_registry


@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


def lex(code, lexicon):
    tokens, errors = tokenize(Source(code), lexicon)
    return tokens, errors


def kinds_and_texts(tokens):
    return [(t.kind, t.text) for t in tokens if t.kind is not K.EOF]


def test_basic_sentence(lexicon):
    tokens, errors = lex("100と200を足して表示する。", lexicon)
    assert errors == []
    assert kinds_and_texts(tokens) == [
        (K.NUMBER, "100"), (K.PARTICLE, "と"), (K.NUMBER, "200"), (K.PARTICLE, "を"),
        (K.VERB, "足して"), (K.VERB, "表示する"), (K.PERIOD, "。"),
    ]
    tashite = tokens[4]
    assert (tashite.verb_id, tashite.lemma, tashite.form) == ("std.足す", "足す", Form.TE)


def test_all_stage1_examples_have_no_errors(lexicon):
    program = """
        「こんにちは」を表示する。
        10から3を引いて表示する。
        3を10から引いて表示する。
        10を2で割って表示する。
        (100+200)×3を表示する。
        2と3を掛けて、10から引いて表示する。
        100に5を足して表示する。
        100と200を足す。それを表示する。
    """
    _, errors = lex(program, lexicon)
    assert errors == []


def test_fullwidth_input_keeps_original_text(lexicon):
    tokens, errors = lex("（１００＋２００）×３を表示する。", lexicon)
    assert errors == []
    assert [t.text for t in tokens[:7]] == ["（", "１００", "＋", "２００", "）", "×", "３"]
    assert tokens[1].value == 100
    assert tokens[5].value == "*"   # 演算子的标准写法


def test_decimal_uses_decimal_not_float(lexicon):
    tokens, _ = lex("0.1と0.2を足す。", lexicon)
    assert tokens[0].value == Decimal("0.1")
    assert tokens[2].value == Decimal("0.2")


def test_string_content_is_original(lexicon):
    tokens, errors = lex("「１００円」を表示する。", lexicon)
    assert errors == []
    assert tokens[0].kind is K.STRING
    assert tokens[0].value == "１００円"


def test_longest_match():
    # 「と」（助词）和「とる」（动词）同时存在时，「とる」必须整体匹配
    registry = Registry()
    registry.add_verb(VerbSpec("test.とる", "とる", Conjugation.GODAN,
                               params=(), returns=None, impl=lambda ctx: None))
    lexicon = Lexicon.from_registry(registry)
    tokens, errors = lex("1とる。", lexicon)
    assert errors == []
    assert kinds_and_texts(tokens) == [(K.NUMBER, "1"), (K.VERB, "とる"), (K.PERIOD, "。")]


def test_positions(lexicon):
    source = Source("100を表示する。\n  それを表示する。")
    tokens, _ = tokenize(source, lexicon)
    sore = next(t for t in tokens if t.kind is K.PRONOUN)
    assert source.line_col(sore.span.start) == (2, 3)


def test_unknown_word_reported_once(lexicon):
    tokens, errors = lex("100とねこを足す。", lexicon)
    assert [(e.code, e.message) for e in errors] == [("E1001", "「ねこ」という言葉はわかりません")]
    # 出错后继续断词
    assert (K.PARTICLE, "を") in kinds_and_texts(tokens)


def test_unclosed_string(lexicon):
    tokens, errors = lex("「閉じてない\n100を表示する。", lexicon)
    assert [e.code for e in errors] == ["E1002"]
    # 下一行不受影响
    assert (K.NUMBER, "100") in kinds_and_texts(tokens)


def test_long_vowel_between_numbers(lexicon):
    _, errors = lex("10ー5を表示する。", lexicon)
    assert [e.code for e in errors] == ["E1003"]


def test_multiple_errors_reported_together(lexicon):
    _, errors = lex("ねこを表示する。\n10ー5を表示する。", lexicon)
    assert [e.code for e in errors] == ["E1001", "E1003"]


# ---------- 阶段 2-1：名字 ----------

@pytest.mark.parametrize("code, names", [
    ("税率を表示する。", ["税率"]),
    ("データを表示する。", ["データ"]),          # 片假名含长音符
    ("A社と第2四半期を足す。", ["A社", "第2四半期"]),
    ("表示件数を表示する。", ["表示件数"]),        # 开头和动词相同，但不是动词
    ("割引額を表示する。", ["割引額"]),
    ("各々を表示する。", ["各々"]),
])
def test_names(lexicon, code, names):
    tokens, errors = lex(code, lexicon)
    assert errors == []
    assert [t.value for t in tokens if t.kind is K.NAME] == names


def test_dictionary_wins_inside_name(lexicon):
    tokens, _ = lex("合計表示する。", lexicon)
    assert kinds_and_texts(tokens) == [(K.NAME, "合計"), (K.VERB, "表示する"), (K.PERIOD, "。")]


def test_fullwidth_name_is_same_as_halfwidth(lexicon):
    tokens, _ = lex("ＩＤ", lexicon)
    assert (tokens[0].text, tokens[0].value) == ("ＩＤ", "ID")


def test_assignment_tokens(lexicon):
    tokens, errors = lex("合計は0とする。", lexicon)
    assert errors == []
    assert kinds_and_texts(tokens) == [
        (K.NAME, "合計"), (K.PARTICLE, "は"), (K.NUMBER, "0"), (K.KEYWORD, "とする"), (K.PERIOD, "。"),
    ]


@pytest.mark.parametrize("word, hint", [
    ("売り上げ", "「売上」のように書いてください"),
    ("支払い", "「支払」のように書いてください"),
    ("お客様", "漢字かカタカナで書いてください"),
])
def test_hiragana_in_name(lexicon, word, hint):
    tokens, errors = lex(f"{word}を表示する。", lexicon)
    assert [e.code for e in errors] == ["E1004"]
    assert errors[0].message.endswith(hint)
    assert tokens[0].kind is K.UNKNOWN


@pytest.mark.parametrize("word", ["計算する", "振り込む", "計算して"])
def test_misspelled_verb_is_not_reported_as_name(lexicon, word):
    _, errors = lex(f"100を{word}。", lexicon)
    assert [e.code for e in errors] == ["E1001"]
