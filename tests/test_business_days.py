"""阶段 4-3a：营业日、加减月份"""

from datetime import date

import pytest

from dandori.api import compile_source, run
from dandori.dates import add_months
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.plugin.registry import VerbError, default_registry
from dandori.runtime.interpreter import Context
from dandori.stdlib.japan.calendar import load_bank_calendar


@pytest.fixture(scope="module")
def calendar():
    return load_bank_calendar()


def run_code(code):
    output = []
    errors = run(Source(code), Context(output=output.append, today=date(2026, 10, 13)))
    return output, [e.code for e in errors]


def show(expr):
    output, errors = run_code(f"{expr}を表示する。")
    assert errors == [], errors
    return output[0]


def compile_codes(code):
    return [e.code for e in compile_source(Source(code)).errors]


# ---------- 银行营业日 ----------

@pytest.mark.parametrize("day, business", [
    (date(2026, 10, 13), True),     # 火
    (date(2026, 10, 17), False),    # 土
    (date(2026, 10, 18), False),    # 日
    (date(2026, 10, 12), False),    # スポーツの日
    (date(2026, 9, 22), False),     # 国民の休日（夹在两个祝日之间）：公式算不出
    (date(2019, 5, 1), False),      # 即位の日
    (date(2020, 7, 24), False),     # 奥运会调整的スポーツの日
    (date(2020, 10, 12), True),     # 2020 年的体育の日被移到了 7 月 24 日
    (date(2026, 12, 31), False),    # 银行的年末年始
    (date(2027, 1, 2), False),
    (date(2027, 1, 4), True),
    (date(2026, 12, 30), True),
])
def test_is_business_day(calendar, day, business):
    assert calendar.is_business_day(day) is business


def test_out_of_range_is_error(calendar):
    with pytest.raises(VerbError) as e:
        calendar.is_business_day(date(2028, 1, 4))
    assert e.value.code == "E5012"


def test_description(calendar):
    assert calendar.description == "内閣府「国民の祝日」（1955年〜2027年）"


# ---------- 写法 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


@pytest.mark.parametrize("code, expected", [
    ("3営業日", [(K.NUMBER, "3営業日")]),
    ("1か月", [(K.NUMBER, "1か月")]),
    ("1ヶ月", [(K.NUMBER, "1ヶ月")]),
    ("1カ月", [(K.NUMBER, "1カ月")]),
    ("1ヵ月", [(K.NUMBER, "1ヵ月")]),
    ("1ケ月", [(K.NUMBER, "1ケ月")]),
    ("2年", [(K.NUMBER, "2年")]),
    ("2026年度", [(K.NUMBER, "2026"), (K.NAME, "年度")]),
    ("休日出勤", [(K.NAME, "休日出勤")]),                  # 「が休日」整体是关键字，不影响名字
    ("期日が休日", [(K.NAME, "期日"), (K.KEYWORD, "が休日")]),
])
def test_tokens(lexicon, code, expected):
    tokens, errors = tokenize(Source(code), lexicon)
    assert errors == []
    assert [(t.kind, t.text) for t in tokens if t.kind is not K.EOF] == expected


@pytest.mark.parametrize("code", ["1.5か月", "0.5営業日", "1.5年"])
def test_units_must_be_integers(lexicon, code):
    _, errors = tokenize(Source(code), lexicon)
    assert [e.code for e in errors] == ["E1013"]


# ---------- 营业日的计算 ----------

@pytest.mark.parametrize("expr, expected", [
    ("2026年10月13日+1営業日", "2026年10月14日"),
    ("2026年10月16日+1営業日", "2026年10月19日"),       # 金 → 下周一
    ("2026年9月18日+1営業日", "2026年9月24日"),         # 5 天连休
    ("2026年9月18日+3営業日", "2026年9月28日"),
    ("2026年12月28日+3営業日", "2027年1月4日"),         # 银行的年末年始
    ("2026年10月19日-1営業日", "2026年10月16日"),
    ("2026年10月13日+0営業日", "2026年10月13日"),
    ("2026年10月17日の翌営業日", "2026年10月19日"),
    ("2026年10月13日の翌営業日", "2026年10月14日"),     # 不含当天
    ("2026年10月13日の前営業日", "2026年10月9日"),      # 10/12 是祝日
])
def test_business_day_arithmetic(expr, expected):
    assert show(expr) == expected


@pytest.mark.parametrize("code, expected", [
    ("2026年11月3日を前倒しして表示する。", "2026年11月2日"),    # 文化の日 → 前一天
    ("2026年11月3日を後倒しして表示する。", "2026年11月4日"),
    ("2026年11月4日を前倒しして表示する。", "2026年11月4日"),    # 营业日不变
    ("2026年5月3日を後倒しして表示する。", "2026年5月7日"),      # 黄金周
    ("2026年10月13日に3営業日を足して表示する。", "2026年10月16日"),
    ("2026年10月16日から1営業日を引いて表示する。", "2026年10月15日"),
])
def test_verbs(code, expected):
    output, errors = run_code(code)
    assert errors == [] and output == [expected]


@pytest.mark.parametrize("cond, expected", [
    ("2026年9月22日が休日", True),
    ("2026年9月22日が営業日", False),
    ("2026年9月24日が休日でない", True),
    ("2026年9月24日が営業日", True),
    ("今日が営業日", True),
])
def test_day_kind_conditions(cond, expected):
    output, errors = run_code(f"もし{cond}ならば、「はい」を表示する。そうでなければ、「いいえ」を表示する。ここまで。")
    assert errors == []
    assert output == ["はい" if expected else "いいえ"]


def test_day_kind_in_table(tmp_path):
    (tmp_path / "a.csv").write_text("番号,期日\n1,2026/10/12\n2,2026/10/13\n3,2026/10/17\n", encoding="utf-8")
    output = []
    errors = run(Source("「a.csv」を読み込んで、表とする。表から期日が休日のものを絞り込んで、その番号を表示する。"),
                 Context(output=output.append, base_dir=tmp_path))
    assert errors == [] and output == ["1、3"]


def test_out_of_range_at_runtime():
    output, errors = run_code("2027年12月30日+3営業日を表示する。")
    assert errors == ["E5012"]


@pytest.mark.parametrize("code, expected", [
    ("もし100が休日ならば、ここまで。", ["E3051"]),
    ("3営業日+1を表示する。", ["E3045"]),
    ("1か月×2を表示する。", ["E3045"]),
    ("-3営業日を表示する。", ["E3045"]),
    ("100に3営業日を足す。", ["E3045"]),
    ("今日の週を表示する。", ["E3049"]),
])
def test_errors(code, expected):
    assert compile_codes(code) == expected


# ---------- 加减月份（民法第 143 条） ----------

@pytest.mark.parametrize("start, months, expected", [
    (date(2026, 1, 31), 1, date(2026, 2, 28)),      # 那个月没有这一天：取月末
    (date(2026, 2, 28), 1, date(2026, 3, 28)),      # 不会变成 3 月 31 日
    (date(2026, 3, 31), -1, date(2026, 2, 28)),
    (date(2026, 11, 30), 3, date(2027, 2, 28)),     # 跨年
    (date(2028, 1, 31), 1, date(2028, 2, 29)),      # 闰年
    (date(2028, 2, 29), 12, date(2029, 2, 28)),
    (date(2026, 10, 13), 0, date(2026, 10, 13)),
])
def test_add_months(start, months, expected):
    assert add_months(start, months) == expected


@pytest.mark.parametrize("expr, expected", [
    ("2026年1月31日+1か月", "2026年2月28日"),
    ("2026年1月31日+1ヶ月", "2026年2月28日"),
    ("2026年3月31日-1か月", "2026年2月28日"),
    ("2028年2月29日+1年", "2029年2月28日"),
    ("2026年10月1日+3営業日+1か月", "2026年11月6日"),
])
def test_month_arithmetic(expr, expected):
    assert show(expr) == expected


# ---------- 説明 ----------

def test_rule_data_is_reported():
    result = compile_source(Source("2026年10月13日の翌営業日を表示する。"))
    assert result.rule_data == ("祝日：内閣府「国民の祝日」（1955年〜2027年）",)
    assert compile_source(Source("2026年10月13日+1か月を表示する。")).rule_data == ()   # 月数不用祝日
