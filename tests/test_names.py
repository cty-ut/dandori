"""阶段 4-4：文字列的规范化（整える）"""

import pytest

from dandori.api import compile_source, run
from dandori.frontend.source import Source
from dandori.runtime.interpreter import Context
from dandori.stdlib.japan.names import DESCRIPTION, normalize_name


@pytest.mark.parametrize("text, expected", [
    # 法人格：全称、括号、㈱
    ("山田商事株式会社", "山田商事"),
    ("株式会社山田商事", "山田商事"),
    ("(株)山田商事", "山田商事"),
    ("（株）山田商事", "山田商事"),
    ("㈱山田商事", "山田商事"),
    ("山田商事有限会社", "山田商事"),
    ("特定非営利活動法人ひまわり", "ヒマワリ"),
    # 银行的写法（全銀フォーマット）
    ("ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ", "ヤマダシヨウジ"),            # 前株
    ("ﾔﾏﾀﾞｼﾖｳｼﾞ(ｶ", "ヤマダシヨウジ"),            # 後株
    ("ﾔﾏﾀﾞｼﾖｳｼﾞ(ｶ)", "ヤマダシヨウジ"),
    ("ｶ) ﾔﾏﾀﾞ ｼﾖｳｼﾞ", "ヤマダシヨウジ"),          # 带空格
    ("ﾕ)ｻﾄｳﾌﾞﾂｻﾝ", "サトウブツサン"),
    ("ﾄﾞ)ｴｲﾁｹｲ", "エイチケイ"),
    ("ｶﾌﾞｼｷｶﾞｲｼｬﾔﾏﾀﾞ", "ヤマダ"),               # 假名全称
    # 假名
    ("ヤマダショウジ", "ヤマダシヨウジ"),          # 小字假名 → 大字
    ("やまだしょうじ", "ヤマダシヨウジ"),          # 平假名 → 片假名
    ("ｷｯﾌﾟ", "キツプ"),
    ("ﾊﾟﾝ", "パン"),                               # 半浊音合并
    # 长音与连字符
    ("シーシー", "シーシー"),
    ("ｼｰｼｰ", "シーシー"),
    ("ABC-JAPAN", "ABCJAPAN"),                     # 片假名之外的连字符是符号
    ("ABC JAPAN", "ABCJAPAN"),
    # 英数
    ("ＡＢＣ", "ABC"),
    ("abc", "ABC"),
    ("ＡＢＣ・ジャパン", "ABCジヤパン"),
    # 异体字、空格
    ("髙島屋", "高島屋"),
    ("山﨑　太郎", "山崎太郎"),
    ("渡邊", "渡辺"),
    ("齋藤", "斎藤"),
    # 不应误删
    ("カシオ計算機", "カシオ計算機"),              # 「カ」开头但不是「カ)」
    ("イトウ", "イトウ"),
    ("株式会社", ""),
])
def test_normalize(text, expected):
    assert normalize_name(text) == expected


@pytest.mark.parametrize("a, b", [
    ("ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ", "ヤマダショウジ"),            # 银行明细 vs フリガナ
    ("ﾔﾏﾀﾞｼﾖｳｼﾞ(ｶ", "やまだしょうじ"),
    ("(株)ABCジャパン", "ＡＢＣ・ジャパン株式会社"),
    ("髙橋商店", "高橋商店"),
])
def test_same_company_matches(a, b):
    assert normalize_name(a) == normalize_name(b)


def test_idempotent():
    for text in ("ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ", "山田商事株式会社", "ＡＢＣ・ジャパン"):
        once = normalize_name(text)
        assert normalize_name(once) == once


def run_code(code, base_dir=None):
    output = []
    ctx = Context(output=output.append)
    if base_dir is not None:
        ctx.base_dir = base_dir
    errors = run(Source(code), ctx)
    return output, [e.code for e in errors]


def test_verb():
    output, errors = run_code("""
        「ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ」を整えて、銀行側とする。
        「ヤマダショウジ」を整えて、マスタ側とする。
        銀行側を表示する。
        もし銀行側がマスタ側ならば、「一致」を表示する。ここまで。
    """)
    assert errors == []
    assert output == ["ヤマダシヨウジ", "一致"]


def test_column_keeps_blanks(tmp_path):
    (tmp_path / "入金.csv").write_text("振込依頼人,金額\nｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ,1\n,2\n", encoding="utf-8")
    output, errors = run_code("「入金.csv」を読み込んで、入金とする。入金の振込依頼人を整えて表示する。",
                              base_dir=tmp_path)
    assert errors == []
    assert output == ["ヤマダシヨウジ、（空欄）"]


def test_type_errors():
    result = compile_source(Source("100を整える。"))
    assert [e.code for e in result.errors] == ["E3007"]


def test_rule_data_version():
    result = compile_source(Source("「a」を整えて表示する。"))
    assert result.rule_data == (f"表記ゆれ規則：{DESCRIPTION}",)
    assert "第1版" in DESCRIPTION
