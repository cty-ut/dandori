import pytest

from dandori.frontend.conjugation import Conjugation, Form, conjugate


@pytest.mark.parametrize("word, conjugation, te", [
    ("足す", Conjugation.GODAN, "足して"),
    ("引く", Conjugation.GODAN, "引いて"),      # イ音便
    ("割る", Conjugation.GODAN, "割って"),      # 促音便
    ("読む", Conjugation.GODAN, "読んで"),      # 撥音便
    ("急ぐ", Conjugation.GODAN, "急いで"),
    ("行く", Conjugation.GODAN, "行って"),      # 例外
    ("掛ける", Conjugation.ICHIDAN, "掛けて"),
    ("表示する", Conjugation.SURU, "表示して"),
])
def test_te_form(word, conjugation, te):
    forms = conjugate(word, conjugation)
    assert forms[Form.DICTIONARY] == word
    assert forms[Form.TE] == te


def test_invalid_ending():
    with pytest.raises(ValueError):
        conjugate("表示", Conjugation.SURU)
