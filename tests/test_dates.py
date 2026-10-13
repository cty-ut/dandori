"""阶段 4-2：日付"""

from datetime import date

import pytest

from dandori.api import compile_source, run
from dandori.cli import main
from dandori.connectors import ConnectorError
from dandori.connectors.csv import read_csv
from dandori.dates import ERA_BY_NAME, InvalidDate, make_date, parse_cell
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context
from dandori.types import Type

TODAY = date(2026, 10, 13)


def run_code(code, base_dir=None, today=TODAY):
    output = []
    ctx = Context(output=output.append, today=today)
    if base_dir is not None:
        ctx.base_dir = base_dir
    errors = run(Source(code), ctx)
    return output, [e.code for e in errors]


def show(expr):
    output, errors = run_code(f"{expr}を表示する。")
    assert errors == [], errors
    return output[0]


def compile_codes(code):
    return [e.code for e in compile_source(Source(code)).errors]


# ---------- 程序中的写法 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.mark.parametrize("code, value", [
    ("2026年10月31日", date(2026, 10, 31)),
    ("2026年1月5日", date(2026, 1, 5)),
    ("令和8年10月31日", date(2026, 10, 31)),
    ("令和元年5月1日", date(2019, 5, 1)),
    ("平成31年4月30日", date(2019, 4, 30)),
    ("昭和64年1月7日", date(1989, 1, 7)),
    ("２０２６年１０月３１日", date(2026, 10, 31)),       # 全角
])
def test_date_literals(lexicon, code, value):
    tokens, errors = tokenize(Source(code), lexicon)
    assert errors == []
    assert (tokens[0].kind, tokens[0].value) == (K.DATE, value)


@pytest.mark.parametrize("code, hint", [
    ("平成32年1月1日", "「令和2年1月1日」のことですか？"),    # 改元后没有更新的系统
    ("令和元年4月1日", "「平成31年4月1日」のことですか？"),
    ("昭和64年1月8日", "「平成元年1月8日」のことですか？"),
    ("2026年2月30日", None),
    ("2026年13月1日", None),
])
def test_invalid_date_literals(lexicon, code, hint):
    _, errors = tokenize(Source(code), lexicon)
    assert [e.code for e in errors] == ["E1012"]
    if hint:
        assert hint in errors[0].message


def test_invalid_date_does_not_cascade():
    assert compile_codes("期日は平成32年1月1日とする。期日を表示する。") == ["E1012"]


@pytest.mark.parametrize("code, expected", [
    ("30日", [(K.NUMBER, "30日")]),                                 # 天数：就是数
    ("2026年度", [(K.NUMBER, "2026"), (K.NAME, "年度")]),           # 不是日期
    ("今日より後", [(K.KEYWORD, "今日"), (K.KEYWORD, "より後")]),
])
def test_tokens(lexicon, code, expected):
    tokens, _ = tokenize(Source(code), lexicon)
    assert [(t.kind, t.text) for t in tokens if t.kind is not K.EOF] == expected


# ---------- 今日 ----------

def test_today_comes_from_context():
    assert run_code("今日を表示する。", today=date(2026, 10, 31))[0] == ["2026年10月31日"]


def test_cli_today_option(capsys, tmp_path):
    program = tmp_path / "p.dandori"
    program.write_text("今日の和暦を表示する。", encoding="utf-8")
    assert main(["実行", str(program), "--今日", "令和8年10月31日"]) == 0
    assert capsys.readouterr().out.strip() == "令和8年10月31日"


def test_today_default_is_jst():
    from datetime import datetime, timedelta, timezone
    from dandori.dates import today_jst
    assert today_jst() == datetime.now(timezone(timedelta(hours=9))).date()


# ---------- 计算 ----------

@pytest.mark.parametrize("expr, expected", [
    ("2026年10月31日+30日", "2026年11月30日"),
    ("30+2026年10月31日", "2026年11月30日"),
    ("2026年10月31日-1日", "2026年10月30日"),
    ("2026年12月31日+1日", "2027年1月1日"),
    ("2026年10月31日-2026年10月13日", "18"),
    ("2026年10月13日-2026年10月31日", "-18"),
    ("2028年2月28日+1日", "2028年2月29日"),      # 闰年
])
def test_arithmetic(expr, expected):
    assert show(expr) == expected


@pytest.mark.parametrize("code, expected", [
    ("2026年10月31日に30日を足して表示する。", "2026年11月30日"),
    ("2026年10月31日から1日を引いて表示する。", "2026年10月30日"),
    ("2026年10月31日から2026年10月1日を引いて表示する。", "30"),
])
def test_verbs(code, expected):
    output, errors = run_code(code)
    assert errors == [] and output == [expected]


@pytest.mark.parametrize("expr", [
    "2026年10月31日+2026年10月31日",
    "2026年10月31日×2",
    "2026年10月31日÷2",
    "2026年10月31日+100円",
    "10-2026年10月31日",
    "-2026年10月31日",
])
def test_invalid_arithmetic(expr):
    assert compile_codes(f"{expr}を表示する。") == ["E3045"]


def test_fractional_days():
    assert run_code("2026年10月31日+1.5を表示する。")[1] == ["E5011"]


# ---------- 比较 ----------

@pytest.mark.parametrize("cond, expected", [
    ("今日が2026年10月13日以前", True),
    ("今日が2026年10月13日以降", True),
    ("今日が2026年10月13日より前", False),
    ("今日が2026年10月13日より後", False),
    ("今日が2026年10月12日より後", True),
    ("今日が令和8年10月13日", True),           # 和暦与西暦是同一个日期
    ("今日>=2026年10月1日", True),             # 符号也可以
    ("今日が2026年10月1日でない", True),
])
def test_comparisons(cond, expected):
    output, errors = run_code(f"もし{cond}ならば、「はい」を表示する。そうでなければ、「いいえ」を表示する。ここまで。")
    assert errors == []
    assert output == ["はい" if expected else "いいえ"]


@pytest.mark.parametrize("cond, expected", [
    ("今日が2026年10月1日以上", ["E3047"]),     # 日付要用「以降」
    ("今日が2026年10月1日未満", ["E3047"]),
    ("100が50以降", ["E3048"]),                # 数要用「以上」
    ("今日が100より後", ["E3013"]),
    ("今日が100", ["E3013"]),
])
def test_comparison_errors(cond, expected):
    assert compile_codes(f"もし{cond}ならば、ここまで。") == expected


# ---------- 属性 ----------

@pytest.mark.parametrize("expr, expected", [
    ("2026年10月31日の年", "2026"),
    ("2026年10月31日の月", "10"),
    ("2026年10月31日の日", "31"),
    ("2026年10月31日の曜日", "土"),
    ("2026年10月13日の曜日", "火"),
    ("2026年10月13日の月初", "2026年10月1日"),
    ("2026年10月13日の月末", "2026年10月31日"),
    ("2028年2月1日の月末", "2028年2月29日"),
    ("2026年4月1日の年度", "2026"),
    ("2027年3月31日の年度", "2026"),           # 4 月开始
    ("2026年10月31日の和暦", "令和8年10月31日"),
    ("2019年5月1日の和暦", "令和元年5月1日"),
    ("2019年4月30日の和暦", "平成31年4月30日"),
    ("2026年10月31日の月末の曜日", "土"),       # 连续使用
])
def test_properties(expr, expected):
    assert show(expr) == expected


def test_unknown_property():
    assert compile_codes("今日の週を表示する。") == ["E3049"]


def test_property_types():
    result = compile_source(Source("今日の月末を表示する。今日の年+1を表示する。今日の曜日を表示する。"))
    assert result.errors == []


# ---------- CSV ----------

@pytest.mark.parametrize("text, value", [
    ("2026/10/31", date(2026, 10, 31)),
    ("2026/1/5", date(2026, 1, 5)),
    ("2026-10-31", date(2026, 10, 31)),
    ("2026.10.31", date(2026, 10, 31)),
    ("2026年10月31日", date(2026, 10, 31)),
    ("令和8年10月31日", date(2026, 10, 31)),
    ("令和元年5月1日", date(2019, 5, 1)),
    ("R8.10.31", date(2026, 10, 31)),
    ("R8/10/31", date(2026, 10, 31)),
    ("r08.10.31", date(2026, 10, 31)),
    ("H31.4.30", date(2019, 4, 30)),
    ("20261031", None),                         # 没有分隔符：不是日期
    ("10/31", None),
])
def test_parse_cell(text, value):
    assert parse_cell(text) == value


def test_csv_date_column_with_mixed_formats(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("日付\n2026/10/01\n令和8年10月2日\nR8.10.3\n\n", encoding="utf-8")
    table = read_csv(path, "a.csv")
    assert table.type.columns == (("日付", Type.DATE),)
    assert table.column("日付") == [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 3)]


def test_csv_nonexistent_date_reports_row(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("番号,日付\n1,2026/10/01\n2,H32.1.1\n", encoding="utf-8")
    with pytest.raises(ConnectorError) as e:
        read_csv(path, "a.csv")
    assert e.value.code == "E3050"
    assert e.value.params["row"] == "3"
    assert "令和2年1月1日" in e.value.params["hint"]


def test_csv_dates_end_to_end(tmp_path):
    (tmp_path / "請求.csv").write_text(
        "番号,支払期日,サイト\nA,2026/10/31,30\nB,R8.11.30,45\nC,H31.4.30,60\n", encoding="utf-8")
    output, errors = run_code("""
        「請求.csv」を読み込んで、請求とする。
        請求から支払期日が今日より前のものを絞り込んで、その番号を表示する。
        請求を支払期日の大きい順に並べて、その番号を表示する。
        請求のそれぞれについて、
          支払期日+サイトを表示する。
        ここまで。
        請求の支払期日の年度を表示する。
        請求を「出力.csv」に書き出す。
    """, base_dir=tmp_path)
    assert errors == []
    assert output == ["C", "B、A、C", "2026年11月30日", "2027年1月14日", "2019年6月29日",
                      "2026、2026、2019"]
    text = (tmp_path / "出力.csv").read_bytes().decode("utf-8-sig")
    assert text.splitlines()[1:] == ["A,2026/10/31,30", "B,2026/11/30,45", "C,2019/04/30,60"]


def test_fill_date_blanks(tmp_path):
    (tmp_path / "a.csv").write_text("番号,期日\n1,2026/10/31\n2,\n", encoding="utf-8")
    output, errors = run_code("「a.csv」を読み込んで、表とする。表の期日の空欄を今日で埋めて、その期日を表示する。",
                              base_dir=tmp_path)
    assert errors == []
    assert output == ["2026年10月31日、2026年10月13日"]


def test_make_date_direct():
    assert make_date(8, 10, 31, ERA_BY_NAME["令和"]) == date(2026, 10, 31)
    with pytest.raises(InvalidDate):
        make_date(2026, 2, 29)
