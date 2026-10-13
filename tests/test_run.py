"""端到端：从源码到输出"""

from pathlib import Path

import pytest

from dandori.api import run
from dandori.frontend.source import Source
from dandori.runtime.interpreter import Context

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_code(code):
    output = []
    errors = run(Source(code), Context(output=output.append))
    return output, [e.code for e in errors]


@pytest.mark.parametrize("code, expected", [
    ("100と200を足して表示する。", "300"),
    ("「こんにちは」を表示する。", "こんにちは"),
    ("10から3を引いて表示する。", "7"),
    ("3を10から引いて表示する。", "7"),
    ("10を2で割って表示する。", "5"),
    ("(100+200)×3を表示する。", "900"),
    ("100+200×3を表示する。", "700"),
    ("10-3-2を表示する。", "5"),
    ("2と3を掛けて、10から引いて表示する。", "4"),
    ("2と3を掛けて、10を引いて表示する。", "-4"),
    ("2と3を掛けて、10を足して表示する。", "16"),
    ("100に5を足して表示する。", "105"),
    ("5に3を掛けて表示する。", "15"),
    ("2と3を掛けて、それを表示する。", "6"),
    ("100と200を足す。それを表示する。", "300"),
    ("１００＋２００を表示する。", "300"),
    ("-5+2を表示する。", "-3"),
    ("1と2と3を表示する。", "1、2、3"),
])
def test_outputs(code, expected):
    output, errors = run_code(code)
    assert errors == []
    assert output == [expected]


def test_decimal_has_no_float_error():
    output, _ = run_code("0.1と0.2を足して表示する。")
    assert output == ["0.3"]


def test_integral_decimal_shown_as_integer():
    output, _ = run_code("1.5に2を掛けて表示する。")
    assert output == ["3"]


def test_division_without_exact_result():
    output, _ = run_code("10を3で割って表示する。")
    assert output[0].startswith("3.333333")


@pytest.mark.parametrize("code", ["10を0で割って表示する。", "10/0を表示する。"])
def test_division_by_zero(code):
    _, errors = run_code(code)
    assert errors == ["E5001"]


def test_compile_errors_prevent_any_execution():
    # 第二句有错时，第一句也不执行
    output, errors = run_code("1を表示する。\nそれを表示する。")
    assert output == []
    assert errors == ["E3006"]


def test_example_file():
    code = (EXAMPLES / "はじめて.dandori").read_text(encoding="utf-8")
    output, errors = run_code(code)
    assert errors == []
    assert output[:3] == ["300", "こんにちは", "7"]


# ---------- 阶段 2-1：变量 ----------

def test_variables():
    output, errors = run_code("""
        税率は0.1とする。
        価格は1000とする。
        価格×税率を表示する。
        価格と価格×税率を足して、税込価格とする。
        税込価格を表示する。
    """)
    assert errors == []
    assert output == ["100", "1100"]


def test_accumulate():
    output, errors = run_code("""
        合計は0とする。
        合計に500を足して、合計とする。
        合計に300を足して、合計とする。
        合計を表示する。
    """)
    assert errors == []
    assert output == ["800"]


def test_fullwidth_and_halfwidth_names_are_same():
    output, errors = run_code("ＩＤは7とする。IDを表示する。")
    assert errors == []
    assert output == ["7"]


def test_variables_example_file():
    code = (EXAMPLES / "変数.dandori").read_text(encoding="utf-8")
    output, errors = run_code(code)
    assert errors == []
    assert output == ["100", "1100", "800", "300"]


def test_declared_terms_as_variables():
    output, errors = run_code("""
        用語：売り上げ、お客様。
        売り上げは0とする。
        売り上げに500を足して、売り上げとする。
        お客様は「山田商事」とする。
        お客様を表示する。
        売り上げを表示する。
    """)
    assert errors == []
    assert output == ["山田商事", "500"]


# ---------- 阶段 2-2：条件 ----------

@pytest.mark.parametrize("total, expected", [
    (2000000, "大口"), (1000000, "大口"),      # 以上：含本数
    (999999, "中口"), (100000, "中口"),
    (99999, "小口"), (0, "小口"),
])
def test_if_chain_first_match_wins(total, expected):
    output, errors = run_code(f"""
        合計は{total}とする。
        もし合計が1000000以上ならば、
          区分は「大口」とする。
        そうでなく合計が100000以上ならば、
          区分は「中口」とする。
        そうでなければ、
          区分は「小口」とする。
        ここまで。
        区分を表示する。
    """)
    assert errors == []
    assert output == [expected]


@pytest.mark.parametrize("cond, expected", [
    ("45が45を超える", False), ("46が45を超える", True),     # 超える：不含本数
    ("20が20未満", False), ("19が20未満", True),
    ("10が10以下", True),
    ("0.1+0.2が0.3", True),                                # Decimal：没有浮点误差
    ("「a」が「a」", True), ("「a」が「b」でない", True),
    ("1≠2", True), ("1<>1", False),
    ("1が1かつ2が3", False), ("1が1または2が3", True),
])
def test_conditions(cond, expected):
    output, errors = run_code(f"もし{cond}ならば、「はい」を表示する。そうでなければ、「いいえ」を表示する。ここまで。")
    assert errors == []
    assert output == ["はい" if expected else "いいえ"]


def test_conditions_example_file():
    code = (EXAMPLES / "条件.dandori").read_text(encoding="utf-8")
    output, errors = run_code(code)
    assert errors == []
    assert output == ["中口", "少数の大きな取引", "通常の承認", "36協定の上限を超えています"]


# ---------- 阶段 2-3：循环 ----------

@pytest.mark.parametrize("code, expected", [
    ("3回繰り返して、「a」を表示する。ここまで。", ["a", "a", "a"]),
    ("0回繰り返して、「a」を表示する。ここまで。", []),
    ("月を1から3まで繰り返して、月を表示する。ここまで。", ["1", "2", "3"]),         # 两端都包含
    ("月を10から1まで繰り返して、月を表示する。ここまで。", []),                       # 开始 > 结束：0 次
    ("月を3から3まで繰り返して、月を表示する。ここまで。", ["3"]),
    ("月を-1から1まで繰り返して、月を表示する。ここまで。", ["-1", "0", "1"]),
])
def test_loop_outputs(code, expected):
    output, errors = run_code(code)
    assert errors == []
    assert output == expected


def test_range_is_evaluated_once():
    # 循环中修改范围用的变量，不影响次数：保证一定结束
    output, errors = run_code("""
        上限は3とする。
        月を1から上限まで繰り返して、
          上限に1を足して、上限とする。
          月を表示する。
        ここまで。
        上限を表示する。
    """)
    assert errors == []
    assert output == ["1", "2", "3", "6"]


def test_integral_decimal_is_accepted():
    output, errors = run_code("1.5×2回繰り返して、「a」を表示する。ここまで。")
    assert errors == []
    assert output == ["a", "a", "a"]


@pytest.mark.parametrize("code, expected", [
    ("0-1回繰り返して、ここまで。", "E5002"),
    ("1.5回繰り返して、ここまで。", "E5002"),
    ("月を1から2.5まで繰り返して、ここまで。", "E5003"),
])
def test_loop_runtime_errors(code, expected):
    _, errors = run_code(code)
    assert errors == [expected]


def test_loops_example_file():
    code = (EXAMPLES / "繰り返し.dandori").read_text(encoding="utf-8")
    output, errors = run_code(code)
    assert errors == []
    assert output == ["おはよう"] * 3 + ["78", "40", "1", "2", "3", "2", "4", "6", "3", "6", "9"]
