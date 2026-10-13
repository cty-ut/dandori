from dandori.frontend.normalize import normalize


def test_fullwidth_digits_and_operators():
    assert normalize("（１００＋２００）×３÷４－５＊６／７") == "(100+200)*3/4-5*6/7"


def test_length_is_preserved():
    text = "１００と２００を足して表示する。「ＡＢＣ」"
    assert len(normalize(text)) == len(text)


def test_string_content_is_untouched():
    assert normalize("「１００円×２」を表示する。") == "「１００円×２」を表示する。"


def test_string_ends_at_newline():
    # 没有闭合的字符串不会把下一行也吞进去
    assert normalize("「あ\n１") == "「あ\n1"


def test_fullwidth_period_only_between_digits():
    assert normalize("３．１４") == "3.14"
    assert normalize("終わり．") == "終わり．"


def test_long_vowel_mark_is_never_converted():
    assert normalize("データ") == "データ"
    assert normalize("10ー5") == "10ー5"


def test_fullwidth_letters():
    assert normalize("ＩＤとｉｄ＿１") == "IDとid_1"
