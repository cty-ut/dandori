"""阶段 3-3：逐行处理"""

from pathlib import Path

import pytest

from dandori.api import compile_source, run
from dandori.frontend import ast
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context

EXAMPLES = Path(__file__).parent.parent / "examples"

MEISAI = "取引先,金額,支払い期日\nA,100,2026-10-31\nB,,\nC,300,2026-11-30\n"
NYUKIN = "振込人,金額\nX,1\n"
READ = "「明細.csv」を読み込んで、明細とする。「入金.csv」を読み込んで、入金とする。"


@pytest.fixture
def data(tmp_path):
    (tmp_path / "明細.csv").write_text(MEISAI, encoding="utf-8")
    (tmp_path / "入金.csv").write_text(NYUKIN, encoding="utf-8")
    return tmp_path


def run_in(directory, code):
    output = []
    errors = run(Source(READ + code), Context(output=output.append, base_dir=directory))
    return output, [e.code for e in errors], errors


def compile_codes(directory, code):
    return [e.code for e in compile_source(Source(READ + code), base_dir=directory).errors]


# ---------- 断词与解析 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.mark.parametrize("code, expected", [
    ("明細のそれぞれについて", [(K.NAME, "明細"), (K.KEYWORD, "のそれぞれについて")]),
    ("繰り返しをやめる", [(K.KEYWORD, "繰り返しをやめる")]),
    ("次に進む", [(K.KEYWORD, "次に進む")]),
    ("次回と次月", [(K.NAME, "次回"), (K.PARTICLE, "と"), (K.NAME, "次月")]),
])
def test_tokens(lexicon, code, expected):
    tokens, errors = tokenize(Source(code), lexicon)
    assert errors == []
    assert [(t.kind, t.text) for t in tokens if t.kind is not K.EOF] == expected


def test_tree(data):
    result = compile_source(Source(READ + "明細のそれぞれについて、次に進む。繰り返しをやめる。ここまで。"),
                            base_dir=data)
    assert result.errors == []
    loop = result.tree.sentences[2]
    assert isinstance(loop, ast.EachLoop)
    assert [type(s) for s in loop.body] == [ast.Continue, ast.Break]


@pytest.mark.parametrize("code, expected", [
    ("明細のそれぞれについて、1を表示する。", ["E2016"]),
    ("3回繰り返して、繰り返しをやめる\nここまで。", ["E2001"]),
])
def test_parse_errors(data, code, expected):
    assert compile_codes(data, code) == expected


# ---------- 意味解析 ----------

@pytest.mark.parametrize("code", [
    "明細のそれぞれについて、金額を表示する。ここまで。",
    "明細のそれぞれについて、「支払い期日」を表示する。ここまで。",
    "明細のそれぞれについて、明細の金額を表示する。ここまで。",          # 整列：与平时相同
    "下限は1とする。明細のそれぞれについて、もし金額が下限以上ならば、取引先を表示する。ここまで。ここまで。",
    "明細のそれぞれについて、入金から振込人が「X」のものを絞り込む。ここまで。",  # 振込人 只在入金中
    "3回繰り返して、繰り返しをやめる。ここまで。",
    "月を1から3まで繰り返して、次に進む。ここまで。",
    "明細のそれぞれについて、もし金額が空欄ならば、次に進む。ここまで。ここまで。",
])
def test_ok(data, code):
    assert compile_codes(data, code) == []


@pytest.mark.parametrize("code, expected", [
    ("繰り返しをやめる。", ["E3035"]),
    ("次に進む。", ["E3035"]),
    ("もし1が1ならば、次に進む。ここまで。", ["E3035"]),
    ("合計は1とする。合計のそれぞれについて、ここまで。", ["E3036"]),
    ("明細のそれぞれについて、金頭を表示する。ここまで。", ["E3025"]),
    ("金額は1とする。明細のそれぞれについて、金額を表示する。ここまで。", ["E3029"]),
    ("明細のそれぞれについて、金額は1とする。ここまで。", ["E3029"]),           # 给列名赋值
    ("明細のそれぞれについて、入金のそれぞれについて、金額を表示する。ここまで。ここまで。", ["E3037"]),
    ("明細のそれぞれについて、入金から金額が1以上のものを絞り込む。ここまで。", ["E3037"]),
    ("明細のそれぞれについて、小計は1とする。ここまで。小計を表示する。", ["E3014"]),  # 可能执行 0 次
    ("明細のそれぞれについて、ここまで。金額を表示する。", ["E3010"]),              # 循环外不是列
])
def test_errors(data, code, expected):
    assert compile_codes(data, code) == expected


def test_nested_loops_with_different_columns(data):
    # 列名不重复时可以嵌套
    output, errors, _ = run_in(data, "明細のそれぞれについて、入金のそれぞれについて、"
                                     "取引先と振込人を表示する。ここまで。ここまで。")
    assert errors == []
    assert output == ["A、X", "B、X", "C、X"]


# ---------- 执行 ----------

def test_current_row(data):
    output, errors, _ = run_in(data, "明細のそれぞれについて、取引先を表示する。ここまで。")
    assert errors == []
    assert output == ["A", "B", "C"]


def test_continue_and_break(data):
    output, errors, _ = run_in(data, """
        明細のそれぞれについて、
          もし金額が空欄ならば、
            次に進む。
          ここまで。
          取引先を表示する。
        ここまで。
        明細のそれぞれについて、
          取引先を表示する。
          繰り返しをやめる。
        ここまで。
    """)
    assert errors == []
    assert output == ["A", "C", "A"]


def test_break_only_innermost(data):
    output, errors, _ = run_in(data, """
        2回繰り返して、
          月を1から3まで繰り返して、
            もし月が2ならば、
              繰り返しをやめる。
            ここまで。
            月を表示する。
          ここまで。
        ここまで。
    """)
    assert errors == []
    assert output == ["1", "1"]


def test_continue_in_range_loop(data):
    output, errors, _ = run_in(data, "月を1から4まで繰り返して、もし月が2ならば、次に進む。ここまで。"
                                     "月を表示する。ここまで。")
    assert errors == []
    assert output == ["1", "3", "4"]


def test_accumulate(data):
    output, errors, _ = run_in(data, """
        合計は0とする。
        明細のそれぞれについて、
          もし金額が空欄でないならば、
            合計に金額を足して、合計とする。
          ここまで。
        ここまで。
        合計を表示する。
    """)
    assert errors == []
    assert output == ["400"]


def test_loop_over_filtered_and_sorted_table(data):
    output, errors, _ = run_in(data, """
        明細から金額が空欄でないものを絞り込んで、金額の大きい順に並べて、対象とする。
        対象のそれぞれについて、取引先を表示する。ここまで。
    """)
    assert errors == []
    assert output == ["C", "A"]


@pytest.mark.parametrize("body", [
    "金額×2を表示する。",                      # 算式
    "-金額を表示する。",
    "金額を足して表示する。",                  # 动词
])
def test_blank_in_calculation_reports_row(data, body):
    _, codes, errors = run_in(data, f"明細のそれぞれについて、{body}ここまで。")
    assert codes == ["E5004"]
    assert "明細.csv の 3行目" in errors[0].message


def test_blank_comparison_in_if_reports_row(data):
    _, codes, errors = run_in(data, "明細のそれぞれについて、もし金額が200以上ならば、ここまで。ここまで。")
    assert codes == ["E5006"]
    assert "明細.csv の 3行目" in errors[0].message


def test_quoted_subject_in_if_is_column(data):
    # 逐行处理中，もし条件主语位置的「」是当前行的列
    output, errors, _ = run_in(data, "明細のそれぞれについて、もし「支払い期日」が空欄ならば、"
                                     "取引先を表示する。ここまで。ここまで。")
    assert errors == []
    assert output == ["B"]


def test_quoted_value_elsewhere_is_text(data):
    # 其他位置的「」依然是文字列：含义不随上下文变化
    output, errors, _ = run_in(data, "明細のそれぞれについて、「支払い期日」を表示する。ここまで。")
    assert errors == []
    assert output == ["支払い期日"] * 3


def test_declared_term_as_column(data):
    # 列名含平假名时：用語声明之后直接写
    output = []
    code = "用語：支払い期日。" + READ + "明細のそれぞれについて、支払い期日を表示する。ここまで。"
    errors = run(Source(code), Context(output=output.append, base_dir=data))
    assert errors == []
    assert output == ["2026年10月31日", "（空欄）", "2026年11月30日"]   # 日付列（阶段 4-2）


def test_quoted_subject_outside_loop_is_text(data):
    # 逐行处理之外，もし条件中的「」依然是文字列
    output, errors, _ = run_in(data, "もし「a」が「a」ならば、1を表示する。ここまで。")
    assert errors == []
    assert output == ["1"]


def test_example_file():
    output = []
    code = (EXAMPLES / "それぞれ.dandori").read_text(encoding="utf-8")
    errors = run(Source(code), Context(output=output.append, base_dir=EXAMPLES))
    assert errors == []
    assert output == ["ボールペン", "コピー用紙", "ファイル", "ＡＢＣ株式会社", "175"]
