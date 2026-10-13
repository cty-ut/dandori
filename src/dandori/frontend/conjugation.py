"""动词活用（语法设计 1.4）

动词定义里只写辞书形和活用类型，所有活用形都由这里的规则推出来。
例如只登记「引く・五段」，就能自动得到「引いて」。
"""

from __future__ import annotations

from enum import Enum


class Conjugation(Enum):
    """活用类型"""

    GODAN = "五段"
    ICHIDAN = "一段"
    SURU = "サ変"


class Form(Enum):
    """活用形（阶段 1 只支持这两种）"""

    DICTIONARY = "辞書形"
    TE = "て形"


# 五段动词的て形：词尾 → 变化后的词尾（音便）
_GODAN_TE = {
    "う": "って", "つ": "って", "る": "って",  # 促音便
    "く": "いて", "ぐ": "いで",                # イ音便
    "す": "して",
    "ぬ": "んで", "ぶ": "んで", "む": "んで",  # 撥音便
}


def conjugate(dictionary_form: str, conjugation: Conjugation) -> dict[Form, str]:
    """辞书形 + 活用类型 → 全部活用形"""
    if conjugation is Conjugation.SURU:
        if not dictionary_form.endswith("する"):
            raise ValueError(f"サ変動詞は「する」で終わる必要があります：{dictionary_form}")
        te = dictionary_form[: -len("する")] + "して"

    elif conjugation is Conjugation.ICHIDAN:
        if not dictionary_form.endswith("る"):
            raise ValueError(f"一段動詞は「る」で終わる必要があります：{dictionary_form}")
        te = dictionary_form[:-1] + "て"

    else:
        ending = dictionary_form[-1]
        if ending not in _GODAN_TE:
            raise ValueError(f"五段動詞の語尾として不正です：{dictionary_form}")
        # 唯一的例外：行く → 行って（不是 行いて）
        if dictionary_form.endswith(("行く", "いく")):
            te = dictionary_form[:-1] + "って"
        else:
            te = dictionary_form[:-1] + _GODAN_TE[ending]

    return {Form.DICTIONARY: dictionary_form, Form.TE: te}
