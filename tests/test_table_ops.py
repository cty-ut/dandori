"""阶段 3-2：表格操作"""

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
from dandori.types import TableType, Type

EXAMPLES = Path(__file__).parent.parent / "examples"

CSV = """取引先,品目,金額,支払い期日
山田商事,ペン,12000,2026-10-31
佐藤物産,用紙,98000,2026-10-31
山田商事,ファイル,15500,2026-11-30
ABC,プリンター,128000,2026-11-30
佐藤物産,トナー,8000,
"""
READ = "「明細.csv」を読み込んで、明細とする。"


@pytest.fixture
def data(tmp_path):
    (tmp_path / "明細.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


def run_in(directory, code):
    output = []
    errors = run(Source(READ + code), Context(output=output.append, base_dir=directory))
    return output, [e.code for e in errors], errors


def lines(output):
    return [line.split() for line in output.split("\n")]


def compile_codes(directory, code):
    return [e.code for e in compile_source(Source(READ + code), base_dir=directory).errors]


# ---------- 断词与解析 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.mark.parametrize("code, expected", [
    ("取引先ごとに", [(K.NAME, "取引先"), (K.PARTICLE, "ごとに")]),
    ("その金額", [(K.PRONOUN, "その"), (K.NAME, "金額")]),
    ("1以上のもの", [(K.NUMBER, "1"), (K.KEYWORD, "以上"), (K.KEYWORD, "のもの")]),
    ("1を超えるもの", [(K.NUMBER, "1"), (K.KEYWORD, "を超える"), (K.KEYWORD, "もの")]),
    ("金額の大きい順", [(K.NAME, "金額"), (K.PARTICLE, "の"), (K.KEYWORD, "大きい順")]),
    ("空欄", [(K.KEYWORD, "空欄")]),
])
def test_tokens(lexicon, code, expected):
    tokens, errors = tokenize(Source(code), lexicon)
    assert errors == []
    assert [(t.kind, t.text) for t in tokens if t.kind is not K.EOF] == expected


def parse_tree(directory, code):
    result = compile_source(Source(READ + code), base_dir=directory)
    assert result.errors == []
    return result.tree.sentences[1].steps


def test_row_filter_detection(data):
    (step,) = parse_tree(data, "明細から金額が10000以上かつ取引先が「ABC」でないものを絞り込む。")
    source, condition = step.args
    assert (source.particle, source.value.name) == ("から", "明細")     # 「明細から」不是条件
    assert isinstance(condition.value, ast.RowFilter)
    assert condition.value.condition.op == "かつ"


def test_quoted_subject_is_column(data):
    (step,) = parse_tree(data, "明細から「支払い期日」が空欄のものを絞り込む。")
    comparison = step.args[1].value.condition
    assert isinstance(comparison.left, ast.Name)                         # 主语的「」→ 列名
    assert comparison.left.name == "支払い期日"
    assert isinstance(comparison.right, ast.Blank)


def test_value_side_string_stays_string(data):
    (step,) = parse_tree(data, "明細から取引先が「ABC」のものを絞り込む。")
    assert isinstance(step.args[1].value.condition.right, ast.String)


def test_sono_and_sort_and_blanks(data):
    steps = parse_tree(data, "明細を金額の大きい順に並べて、その金額を合計する。")
    assert isinstance(steps[0].args[1].value, ast.SortKey)
    sono = steps[1].args[0].value
    assert isinstance(sono, ast.ColumnRef) and isinstance(sono.table, ast.Sore)
    (fill,) = parse_tree(data, "明細の金額の空欄を0で埋める。")
    assert isinstance(fill.args[0].value, ast.BlanksOf)


@pytest.mark.parametrize("code, expected", [
    ("その。", ["E2019"]),
    ("明細から金額が1以上かつのものを絞り込む。", ["E2015"]),
])
def test_parse_errors(data, code, expected):
    assert compile_codes(data, code) == expected


# ---------- 意味解析 ----------

def test_result_types(data):
    result = compile_source(Source(READ + "明細の金額を取引先ごとに合計して、集計とする。"
                                    "明細を数えて、件数とする。"), base_dir=data)
    assert result.errors == []
    program = result.program.sentences
    grouped = program[1].steps[0].result_type
    assert grouped == TableType((("取引先", Type.STRING), ("金額", Type.NUMBER)))
    assert program[2].steps[0].result_type is Type.NUMBER


def test_result_columns_are_checked(data):
    # 汇总结果的列也在编译时检查
    assert compile_codes(data, "明細を取引先ごとに数えて、件数表とする。件数表の金額を表示する。") == ["E3025"]
    assert compile_codes(data, "明細を取引先ごとに数えて、件数表とする。件数表の件数を表示する。") == []


def test_variables_usable_in_condition(data):
    assert compile_codes(data, "下限は10000とする。明細から金額が下限以上のものを絞り込む。") == []


@pytest.mark.parametrize("code, expected", [
    # 列与变量重名
    ("金額は1とする。明細から金額が1以上のものを絞り込む。", ["E3029"]),
    ("取引先は1とする。明細から取引先を取り出す。", ["E3029"]),
    ("金額は1とする。明細を金額の大きい順に並べる。", ["E3029"]),
    # 列不存在
    ("明細から金頭が1以上のものを絞り込む。", ["E3025"]),
    ("明細から取引先と金頭を取り出す。", ["E3025"]),
    ("明細の金額を品名ごとに合計する。", ["E3025"]),
    ("明細を金頭の大きい順に並べる。", ["E3025"]),
    ("明細から「支払期日」が空欄のものを絞り込む。", ["E3025"]),
    # 参数的种类不对
    ("明細から100を取り出す。", ["E3030"]),
    ("明細から100を絞り込む。", ["E3031"]),
    ("明細を金額に並べる。", ["E3031"]),
    ("明細の金額を0で埋める。", ["E3031"]),
    ("明細を合計する。", ["E3031"]),
    # 类型
    ("明細の取引先を合計する。", ["E3007"]),
    ("100から金額が1以上のものを絞り込む。", ["E3007"]),
    ("明細の金額の空欄を「なし」で埋める。", ["E3034"]),
    # 空欄
    ("明細から金額が空欄以上のものを絞り込む。", ["E3032"]),
    ("空欄を表示する。", ["E3033"]),
])
def test_compile_errors(data, code, expected):
    assert compile_codes(data, code) == expected


# ---------- 自动填入（语法设计 9.6） ----------

@pytest.mark.parametrize("code", [
    "明細から金額が1以上のものを絞り込んで、数えて、表示する。",          # 必需槽 を（可选槽 ごとに 不填）
    "明細を金額の大きい順に並べて、取引先と金額を取り出して、表示する。",  # から
    "明細から取引先と金額を取り出して、金額が1以上のものを絞り込む。",     # から
    "明細から取引先と金額を取り出して、金額の小さい順に並べる。",          # を
])
def test_auto_fill_in_pipelines(data, code):
    assert compile_codes(data, code) == []


def test_auto_fill_unchanged_for_arithmetic():
    output = []
    errors = run(Source("2と3を掛けて、10を足して表示する。"), Context(output=output.append))
    assert errors == [] and output == ["16"]                             # 可选槽 に，与阶段 1 相同


def test_no_fill_into_column_slots(data):
    # 上一步的表不会被填进「ごとに」：没有可填的"值"槽 → E3005
    assert compile_codes(data, "明細を数えて、明細を数える。") == ["E3005"]


# ---------- 执行 ----------

def test_pipeline(data):
    output, errors, _ = run_in(data, "明細から金額が10000以上のものを絞り込んで、"
                                     "その金額を取引先ごとに合計して、表示する。")
    assert errors == []
    assert lines(output[0]) == [["取引先", "金額"], ["山田商事", "27500"],
                                ["佐藤物産", "98000"], ["ABC", "128000"]]   # 按第一次出现的顺序


@pytest.mark.parametrize("code, expected", [
    ("明細を数えて、表示する。", "5"),
    ("明細の金額を合計して、表示する。", "261500"),
    ("明細の金額を平均して、表示する。", "52300"),
    ("明細から金額が1000000以上のものを絞り込んで、その金額を合計して、表示する。", "0"),  # 空表
    ("明細から金額が1000000以上のものを絞り込んで、その金額を足して表示する。", "0"),
    ("明細から金額が1000000以上のものを絞り込んで、数えて、表示する。", "0"),
])
def test_scalar_results(data, code, expected):
    output, errors, _ = run_in(data, code)
    assert errors == []
    assert output == [expected]


def test_group_count_with_two_keys(data):
    output, errors, _ = run_in(data, "明細を取引先と「支払い期日」ごとに数えて、表示する。")
    assert errors == []
    assert lines(output[0])[0] == ["取引先", "支払い期日", "件数"]
    assert len(lines(output[0])) == 6                                    # 5 组 + 見出し


def test_select_keeps_given_order(data):
    output, errors, _ = run_in(data, "明細から金額と取引先を取り出して、表示する。")
    assert errors == []
    assert lines(output[0])[:2] == [["金額", "取引先"], ["12000", "山田商事"]]


def test_sort_desc_stable_and_blanks_last(data):
    output, errors, _ = run_in(data, "明細を「支払い期日」の小さい順に並べて、"
                                     "取引先と品目を取り出して、表示する。")
    assert errors == []
    assert [row[1] for row in lines(output[0])[1:]] == ["ペン", "用紙", "ファイル", "プリンター", "トナー"]


def test_sort_numbers(data):
    output, errors, _ = run_in(data, "明細を金額の大きい順に並べて、その金額を表示する。")
    assert errors == []
    assert output == ["128000、98000、15500、12000、8000"]


def test_blank_filter_and_fill(data):
    output, errors, _ = run_in(data, """
        明細から「支払い期日」が空欄のものを絞り込んで、数えて、表示する。
        明細から「支払い期日」が空欄でないものを絞り込んで、数えて、表示する。
        明細の「支払い期日」の空欄を「未定」で埋めて、その「支払い期日」を表示する。
    """)
    assert errors == []
    assert output == ["1", "4", "2026-10-31、2026-10-31、2026-11-30、2026-11-30、未定"]


def test_original_table_is_unchanged(data):
    output, errors, _ = run_in(data, "明細から金額が100000以上のものを絞り込む。明細を数えて、表示する。")
    assert errors == []
    assert output == ["5"]


BLANK_CSV = "取引先,金額\nA,100\nB,\nC,300\n"


@pytest.fixture
def blank_data(tmp_path):
    (tmp_path / "明細.csv").write_text(BLANK_CSV, encoding="utf-8")
    return tmp_path


def test_sum_with_blank_reports_row_and_hint(blank_data):
    _, codes, errors = run_in(blank_data, "明細の金額を合計して、表示する。")
    assert codes == ["E5004"]
    assert "明細.csv の 3行目" in errors[0].message


def test_grouped_sum_with_blank_reports_row(blank_data):
    _, codes, errors = run_in(blank_data, "明細の金額を取引先ごとに合計して、表示する。")
    assert codes == ["E5004"]
    assert "明細.csv の 3行目" in errors[0].message


def test_compare_blank_reports_row(blank_data):
    _, codes, errors = run_in(blank_data, "明細から金額が200以上のものを絞り込む。")
    assert codes == ["E5006"]
    assert "明細.csv の 3行目" in errors[0].message


def test_katsu_guards_blank_comparison(blank_data):
    output, errors, _ = run_in(blank_data, "明細から金額が空欄でないかつ金額が200以上のものを"
                                           "絞り込んで、数えて、表示する。")
    assert errors == []
    assert output == ["1"]


def test_fill_then_sum(blank_data):
    output, errors, _ = run_in(blank_data, "明細の金額の空欄を0で埋めて、その金額を合計して、表示する。")
    assert errors == []
    assert output == ["400"]


def test_average_of_empty_table(data):
    _, codes, _ = run_in(data, "明細から金額が1000000以上のものを絞り込んで、その金額を平均する。")
    assert codes == ["E5007"]


def test_example_file():
    output = []
    code = (EXAMPLES / "集計.dandori").read_text(encoding="utf-8")
    errors = run(Source(code), Context(output=output.append, base_dir=EXAMPLES))
    assert errors == []
    assert lines(output[0])[1] == ["山田商事", "27500"]
    assert output[-1] == "58060"
