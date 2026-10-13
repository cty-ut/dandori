"""阶段 4-1：金額"""

from decimal import Decimal

import pytest

from dandori.api import compile_source, run
from dandori.connectors.csv import read_csv
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.money import Money
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context
from dandori.types import Type


def run_code(code, base_dir=None):
    output = []
    ctx = Context(output=output.append) if base_dir is None else Context(output=output.append, base_dir=base_dir)
    errors = run(Source(code), ctx)
    return output, [e.code for e in errors]


def compile_codes(code, base_dir=None):
    return [e.code for e in compile_source(Source(code), base_dir=base_dir).errors]


# ---------- 写法 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.mark.parametrize("code, value", [
    ("100円", Money(100)),
    ("1,000円", Money(1000)),
    ("1,234,567円", Money(1234567)),
    ("100万円", Money(1_000_000)),
    ("1億2000万円", Money(120_000_000)),
    ("1億2000万3000円", Money(120_003_000)),
    ("3千円", Money(3000)),
    ("1千万円", Money(10_000_000)),            # 连写的单位相乘
    ("5百円", Money(500)),
    ("¥1,000", Money(1000)),
    ("￥１，０００", Money(1000)),             # 全角
    ("１００万円", Money(1_000_000)),
    ("100万", 1_000_000),                      # 不带円：数
    ("1.5万", 15000),
    ("1,000", 1000),
    ("0.5円", Money(Decimal("0.5"))),
])
def test_literals(lexicon, code, value):
    tokens, errors = tokenize(Source(code), lexicon)
    assert errors == []
    assert len(tokens) == 2 and tokens[0].kind is K.NUMBER
    assert tokens[0].value == value
    assert type(tokens[0].value) is type(value)


def test_units_must_decrease(lexicon):
    _, errors = tokenize(Source("2000万1億円"), lexicon)
    assert [e.code for e in errors] == ["E1011"]


@pytest.mark.parametrize("code, expected", [
    ("万年筆", [(K.NAME, "万年筆")]),                       # 单位只在紧跟数字时才算
    ("1,23", [(K.NUMBER, "1"), (K.UNKNOWN, ","), (K.NUMBER, "23")]),   # 千位分隔符后必须恰好 3 位
    ("3万件", [(K.NUMBER, "3万"), (K.NAME, "件")]),
])
def test_literal_boundaries(lexicon, code, expected):
    tokens, _ = tokenize(Source(code), lexicon)
    assert [(t.kind, t.text) for t in tokens if t.kind is not K.EOF] == expected


# ---------- 计算（宽松的规则） ----------

@pytest.mark.parametrize("code, expected", [
    ("100円+200円", "300円"),
    ("100円+5", "105円"),                     # 不带单位的数视为同一个单位
    ("5+100円", "105円"),
    ("100円-300円", "-200円"),
    ("100円×3", "300円"),
    ("3×100円", "300円"),
    ("100円÷4", "25円"),
    ("100万円÷50万円", "2"),                  # 金額÷金額 是比例
    ("-500円", "-500円"),
    ("1億2000万円-1円", "119,999,999円"),
])
def test_arithmetic(code, expected):
    output, errors = run_code(f"{code}を表示する。")
    assert errors == []
    assert output == [expected]


@pytest.mark.parametrize("code, expected", [
    ("100円と200円を足して表示する。", "300円"),
    ("100円と5を足して表示する。", "105円"),
    ("1000円から1を引いて表示する。", "999円"),
    ("100円に3を掛けて表示する。", "300円"),
    ("100万円を4で割って表示する。", "250,000円"),
    ("100万円を50万円で割って表示する。", "2"),
])
def test_verbs(code, expected):
    output, errors = run_code(code)
    assert errors == []
    assert output == [expected]


@pytest.mark.parametrize("code, expected", [
    ("100円×2円を表示する。", ["E3043"]),
    ("5÷100円を表示する。", ["E3044"]),
    ("100円と200円を掛ける。", ["E3043"]),
    ("5を100円で割る。", ["E3044"]),
    ("3円回繰り返して、ここまで。", ["E3015"]),
    ("「a」+100円を表示する。", ["E3008"]),
])
def test_static_errors(code, expected):
    assert compile_codes(code) == expected


def test_result_types():
    result = compile_source(Source("100円と5を足して、合計とする。100万円を50万円で割って、比率とする。"
                                   "合計+比率を表示する。"))
    assert result.errors == []
    steps = [s.steps[0] for s in result.program.sentences if hasattr(s, "steps")]
    assert steps[0].result_type is Type.MONEY
    assert steps[1].result_type is Type.NUMBER


# ---------- 比较 ----------

@pytest.mark.parametrize("cond, expected", [
    ("100万円が999999を超える", True),         # 金額与数比较
    ("100万円が1000000", True),
    ("100円が100円", True),
    ("100円が99円以下", False),
])
def test_comparisons(cond, expected):
    output, errors = run_code(f"もし{cond}ならば、「はい」を表示する。そうでなければ、「いいえ」を表示する。ここまで。")
    assert errors == []
    assert output == ["はい" if expected else "いいえ"]


def test_compare_money_with_string_is_error():
    assert compile_codes("もし100円が「100円」ならば、ここまで。") == ["E3013"]


# ---------- 端数处理（与 Excel 一致） ----------

@pytest.mark.parametrize("value, down, half_up, up", [
    ("1.4", "1", "1", "2"),
    ("1.5", "1", "2", "2"),
    ("2.5", "2", "3", "3"),       # Python 的 round(2.5) 是 2（银行家舍入）；Excel 的 ROUND 是 3
    ("1.9", "1", "2", "2"),
    ("-1.5", "-1", "-2", "-2"),   # 负数：向零 / 远离零，与 Excel 一致
    ("-2.5", "-2", "-3", "-3"),
    ("-1.1", "-1", "-1", "-2"),
    ("3", "3", "3", "3"),
])
def test_rounding_matches_excel(value, down, half_up, up):
    for verb, expected in (("切り捨てて", down), ("四捨五入して", half_up), ("切り上げて", up)):
        output, errors = run_code(f"{value}を{verb}表示する。")
        assert errors == [], verb
        assert output == [expected], verb


def test_rounding_money():
    output, errors = run_code("""
        単価は1,001円とする。
        単価×1.1を切り捨てて表示する。
        単価×1.1を四捨五入して表示する。
        単価×1.1を切り上げて表示する。
    """)
    assert errors == []
    assert output == ["1,101円", "1,101円", "1,102円"]


def test_rounding_list():
    output, errors = run_code("1.5円と2.5円を四捨五入して表示する。")
    assert errors == []
    assert output == ["2円、3円"]


def test_fraction_cannot_be_displayed():
    output, errors = run_code("1,001円×1.1を表示する。")
    assert output == [] and errors == ["E5010"]


def test_fraction_in_comparison_is_exact():
    output, errors = run_code("もし1,001円×1.1が1101.1円ならば、「一致」を表示する。ここまで。")
    assert errors == [] and output == ["一致"]


def test_fraction_in_intermediate_is_fine():
    # 中途带零头没关系，最后取整就行
    output, errors = run_code("1,001円×1.1×10を表示する。")
    assert errors == [] and output == ["11,011円"]


# ---------- CSV ----------

def write(tmp_path, text, name="a.csv"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_csv_money_and_triangle_negatives(tmp_path):
    table = read_csv(write(tmp_path, '金額,前年比\n"1,200円",△5\n"△300円",▲12\n¥450,3\n500,0\n'), "a.csv")
    assert table.type.columns == (("金額", Type.MONEY), ("前年比", Type.NUMBER))
    assert table.column("金額") == [Money(1200), Money(-300), Money(450), Money(500)]
    assert all(isinstance(v, Money) for v in table.column("金額"))   # 不带单位的 500 也视为日元
    assert table.column("前年比") == [-5, -12, 3, 0]


def test_csv_plain_numbers_stay_numbers(tmp_path):
    table = read_csv(write(tmp_path, "金額\n1000\n2000\n"), "a.csv")
    assert table.type.columns == (("金額", Type.NUMBER),)


def test_csv_money_operations(tmp_path):
    write(tmp_path, '取引先,金額\nA,"1,000円"\nB,△500円\nA,2000\nC,\n', "明細.csv")
    output, errors = run_code("""
        「明細.csv」を読み込んで、明細とする。
        明細の金額の空欄を0で埋めて、その金額を取引先ごとに合計して、表示する。
        明細から金額が空欄でないかつ金額が0円未満のものを絞り込んで、数えて、表示する。
        明細から金額が空欄でないものを絞り込んで、その金額を平均して、四捨五入して表示する。
    """, base_dir=tmp_path)
    assert errors == []
    assert [line.split() for line in output[0].split("\n")[1:]] == [["A", "3,000円"], ["B", "-500円"], ["C", "0円"]]
    assert output[1:] == ["1", "833円"]


def test_csv_write_money_as_plain_numbers(tmp_path):
    write(tmp_path, '金額\n"1,000円"\n△500円\n', "明細.csv")
    _, errors = run_code("「明細.csv」を読み込んで、明細とする。明細を「出力.csv」に書き出す。", base_dir=tmp_path)
    assert errors == []
    assert (tmp_path / "出力.csv").read_bytes().decode("utf-8-sig") == "金額\r\n1000\r\n-500\r\n"


def test_cannot_write_fractional_money(tmp_path):
    write(tmp_path, "取引先,金額\nA,100円\nA,1円\nB,1円\n", "明細.csv")
    _, errors = run_code("「明細.csv」を読み込んで、明細とする。"
                         "明細の金額を取引先ごとに平均して、「平均.csv」に書き出す。", base_dir=tmp_path)
    assert errors == ["E5010"]                    # 50.5円
    assert not (tmp_path / "平均.csv").exists()


def test_fill_numeric_into_money_column(tmp_path):
    write(tmp_path, "取引先,金額\nA,100円\nB,\n", "明細.csv")
    output, errors = run_code("「明細.csv」を読み込んで、明細とする。明細の金額の空欄を0で埋めて、"
                              "その金額を表示する。", base_dir=tmp_path)
    assert errors == []
    assert output == ["100円、0円"]
