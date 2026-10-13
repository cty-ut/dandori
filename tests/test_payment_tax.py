"""阶段 4-3b：締め・支払、消費税"""

from datetime import date
from decimal import Decimal

import pytest

from dandori.api import compile_source, run
from dandori.frontend.source import Source
from dandori.runtime.interpreter import Context
from dandori.stdlib.japan.payment import parse_terms
from dandori.stdlib.japan.tax import reduced_rate, standard_rate


def run_code(code, base_dir=None):
    output = []
    ctx = Context(output=output.append, today=date(2026, 10, 13))
    if base_dir is not None:
        ctx.base_dir = base_dir
    errors = run(Source(code), ctx)
    return output, [e.code for e in errors]


def compile_codes(code):
    return [e.code for e in compile_source(Source(code)).errors]


# ---------- 支払条件 ----------

@pytest.mark.parametrize("day, terms, expected", [
    (date(2026, 10, 20), "月末締め翌月末払い", date(2026, 11, 30)),
    (date(2026, 10, 31), "月末締め翌月末払い", date(2026, 11, 30)),
    (date(2026, 10, 20), "20日締め翌月10日払い", date(2026, 11, 10)),     # 締め日当天：当月締め
    (date(2026, 10, 21), "20日締め翌月10日払い", date(2026, 12, 10)),     # 晚于締め日：次月締め
    (date(2026, 12, 20), "月末締め翌々月30日払い", date(2027, 2, 28)),    # 2 月没有 30 日：取月末
    (date(2026, 10, 10), "15日締め当月末払い", date(2026, 10, 31)),
    (date(2026, 1, 10), "月末締め3か月後10日払い", date(2026, 4, 10)),
    (date(2026, 1, 10), "末締め翌月末払い", date(2026, 2, 28)),            # 「末締め」也可以
    (date(2026, 11, 25), "２０日締め翌月１０日払い", date(2027, 1, 10)),   # 全角数字
    (date(2026, 1, 31), "31日締め翌月31日払い", date(2026, 2, 28)),
])
def test_payment_date(day, terms, expected):
    assert parse_terms(terms).payment_date(day) == expected


@pytest.mark.parametrize("terms", [
    "月末締め翌月払い",          # 缺少支払日
    "月末締め",
    "翌月末払い",
    "32日締め翌月10日払い",
    "月末締め翌月0日払い",
    "",
])
def test_invalid_terms(terms):
    assert parse_terms(terms) is None


def test_payment_verb_and_holiday_handling():
    output, errors = run_code("""
        請求日は2026年10月20日とする。
        請求日を「20日締め翌月3日払い」で支払日にして表示する。
        請求日を「20日締め翌月3日払い」で支払日にして、前倒しして、表示する。
        請求日を「20日締め翌月3日払い」で支払日にして、後倒しして、表示する。
    """)
    assert errors == []
    # 2026年11月3日是文化の日：原始日期 / 前倒し / 後倒し
    assert output == ["2026年11月3日", "2026年11月2日", "2026年11月4日"]


def test_invalid_terms_literal_is_compile_error():
    assert compile_codes("今日を「月末締め翌月払い」で支払日にする。") == ["E3052"]


def test_terms_from_csv(tmp_path):
    (tmp_path / "請求.csv").write_text(
        "取引先,請求日,支払条件\nA,2026/10/20,月末締め翌月末払い\nB,2026/10/21,20日締め翌月10日払い\n",
        encoding="utf-8")
    output, errors = run_code("""
        「請求.csv」を読み込んで、請求とする。
        請求のそれぞれについて、
          請求日を支払条件で支払日にして、前倒しして、表示する。
        ここまで。
    """, base_dir=tmp_path)
    assert errors == []
    assert output == ["2026年11月30日", "2026年12月10日"]


def test_invalid_terms_from_csv_is_runtime_error(tmp_path):
    (tmp_path / "請求.csv").write_text("請求日,支払条件\n2026/10/20,翌月払い\n", encoding="utf-8")
    _, errors = run_code("「請求.csv」を読み込んで、請求とする。"
                         "請求のそれぞれについて、請求日を支払条件で支払日にして表示する。ここまで。",
                         base_dir=tmp_path)
    assert errors == ["E5013"]


# ---------- 消費税 ----------

@pytest.mark.parametrize("day, standard, reduced", [
    (date(1989, 4, 1), "0.03", "0.03"),
    (date(1997, 3, 31), "0.03", "0.03"),
    (date(1997, 4, 1), "0.05", "0.05"),
    (date(2014, 4, 1), "0.08", "0.08"),
    (date(2019, 9, 30), "0.08", "0.08"),
    (date(2019, 10, 1), "0.10", "0.08"),       # 軽減税率开始
    (date(2026, 10, 13), "0.10", "0.08"),
])
def test_rates(day, standard, reduced):
    assert standard_rate(day) == Decimal(standard)
    assert reduced_rate(day) == Decimal(reduced)


def test_before_consumption_tax():
    _, errors = run_code("1989年3月31日の標準税率を表示する。")
    assert errors == ["E5014"]


@pytest.mark.parametrize("code, expected", [
    ("1,001円×2026年10月20日の標準税率を切り捨てて表示する。", "100円"),
    ("1,001円×2019年9月30日の標準税率を切り捨てて表示する。", "80円"),     # 旧发票：当时的税率
    ("1,001円×2026年10月20日の軽減税率を切り捨てて表示する。", "80円"),
    ("1,001円×10%を切り上げて表示する。", "101円"),
    ("1,001円×10%を四捨五入して表示する。", "100円"),
    ("10%を表示する。", "0.1"),
    ("８％を表示する。", "0.08"),
    ("100%を表示する。", "1"),
    ("2026年10月20日の標準税率を表示する。", "0.1"),
])
def test_tax_calculation(code, expected):
    output, errors = run_code(code)
    assert errors == [] and output == [expected]


def test_tax_fraction_must_be_rounded():
    _, errors = run_code("1,001円×10%を表示する。")
    assert errors == ["E5010"]                  # 100.1円：端数处理必须明确写出


def test_rule_data_in_explain():
    result = compile_source(Source("今日の標準税率を表示する。"))
    assert result.rule_data == ("消費税率：消費税法（3%：1989年4月〜、5%：1997年4月〜、"
                                "8%：2014年4月〜、10%・軽減8%：2019年10月〜）",)
