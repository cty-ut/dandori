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
