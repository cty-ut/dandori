"""阶段 8-1：実行の予定（平台设计.md 第 3 节）"""

from datetime import date, datetime

import pytest

from dandori import api
from dandori.api import Context, Source, compile_source, run
from dandori.dates import JST
from dandori.plugin.registry import VerbError
from dandori.stdlib.japan.calendar import load_bank_calendar
from dandori.stdlib.japan.schedule import parse_schedule


@pytest.fixture(scope="module")
def bank():
    return load_bank_calendar()


def at(y, m, d, hour=0, minute=0):
    return datetime(y, m, d, hour, minute, tzinfo=JST)


def following(text, after, bank, company=None):
    return parse_schedule(text).next_after(after, bank, company)


def codes(code, base_dir=None):
    return [e.code for e in compile_source(Source(code), base_dir=base_dir).errors]


# ---------- 写法 ----------

@pytest.mark.parametrize("text, described", [
    ("毎日 9時", "毎日 9時00分"),
    ("毎営業日 18時", "毎営業日 18時00分"),
    ("毎週月曜日 9時30分", "毎週月曜日 9時30分（休日でも実行します）"),
    ("毎月25日 9時", "毎月25日 9時00分（休日でも実行します）"),
    ("毎月25日（休日なら前営業日） 9時", "毎月25日 9時00分（休日なら前営業日）"),
    ("毎月末日（休日なら翌営業日）18:00", "毎月末日 18時00分（休日なら翌営業日）"),
    ("毎月第3営業日 9時半", "毎月第3営業日 9時30分"),
    ("毎月最終営業日 17時", "毎月最終営業日 17時00分"),
    ("毎月２５日　９時", "毎月25日 9時00分（休日でも実行します）"),     # 全角
])
def test_describe(text, described):
    assert parse_schedule(text).describe() == described


@pytest.mark.parametrize("text, code", [
    ("毎月25日", "E3063"),                          # 时刻必须写
    ("毎月31日 9時", "E3064"),                      # 有的月份没有：用「毎月末日」
    ("毎月29日", "E3064"),                          # 日子的问题先报
    ("毎営業日（休日なら前営業日） 9時", "E3065"),   # 本来就是营业日
    ("毎日（休日なら翌営業日） 9時", "E3065"),
    ("毎月25日 25時", "E3062"),
    ("毎月0日 9時", "E3062"),
    ("毎月第20営業日 9時", "E3062"),
    ("月末 9時", "E3062"),
    ("毎月25日 9時 ごろ", "E3062"),
])
def test_bad_schedule(text, code):
    with pytest.raises(VerbError) as e:
        parse_schedule(text)
    assert e.value.code == code


# ---------- 下次运行时间 ----------

def test_monthly_with_holiday_shift(bank):
    text = "毎月25日（休日なら前営業日） 9時"
    assert following(text, at(2026, 10, 14), bank) == at(2026, 10, 23, 9)      # 25日是星期日 → 23日（金）
    assert following(text, at(2026, 10, 23, 8, 59), bank) == at(2026, 10, 23, 9)
    assert following(text, at(2026, 10, 23, 9), bank) == at(2026, 11, 25, 9)   # 不含 after 本身


def test_monthly_without_shift_runs_on_holiday(bank):
    assert following("毎月25日 9時", at(2026, 10, 14), bank) == at(2026, 10, 25, 9)


def test_shift_crosses_month(bank):
    # 2027年1月31日是星期日 → 2月1日；2月28日是星期日 → 3月1日
    text = "毎月末日（休日なら翌営業日） 18時"
    first = following(text, at(2027, 1, 15), bank)
    assert first == at(2027, 2, 1, 18)
    assert following(text, first, bank) == at(2027, 3, 1, 18)


def test_month_end_february(bank):
    assert following("毎月末日 9時", at(2026, 2, 1), bank) == at(2026, 2, 28, 9)


def test_business_days(bank):
    assert following("毎営業日 9時", at(2026, 10, 16, 10), bank) == at(2026, 10, 19, 9)   # 金 → 月
    assert following("毎営業日 9時", at(2026, 12, 30, 10), bank) == at(2027, 1, 4, 9)    # 银行的年末年始


def test_weekly_shift(bank):
    # 2026年10月12日（月）是スポーツの日 → 13日（火）
    assert following("毎週月曜日（休日なら翌営業日） 9時", at(2026, 10, 5, 10), bank) == at(2026, 10, 13, 9)


def test_nth_and_last_business_day(bank):
    # 2026年11月：2日（月）、3日は文化の日、4日、5日 → 第3営業日は5日
    assert following("毎月第3営業日 9時", at(2026, 11, 1), bank) == at(2026, 11, 5, 9)
    # 2026年10月31日是星期六 → 最终营业日是30日
    assert following("毎月最終営業日 17時", at(2026, 10, 14), bank) == at(2026, 10, 30, 17)


def test_company_calendar(bank):
    company = bank.with_company_holidays({date(2026, 11, 2)})
    assert following("毎月第3会社営業日 9時", at(2026, 11, 1), bank, company) == at(2026, 11, 6, 9)


# ---------- 设定文 ----------

def test_setting_compiles_and_runs_normally():
    code = "実行の予定は、「毎月25日（休日なら前営業日） 9時」とする。\n100と200を足して表示する。"
    result = compile_source(Source(code))
    assert result.errors == []
    assert result.schedule.describe() == "毎月25日 9時00分（休日なら前営業日）"
    assert any(line.startswith("祝日") for line in result.rule_data)
    output = []
    assert run(Source(code), Context(output=output.append)) == []    # 手动执行时没有作用
    assert output == ["300"]


def test_rule_data_only_when_calendar_used():
    result = compile_source(Source("実行の予定は、「毎日 9時」とする。"))
    assert result.schedule is not None and result.rule_data == ()


def test_setting_errors():
    assert codes("実行の予定は、「毎月31日 9時」とする。") == ["E3064"]
    assert codes("実行の予定は、「毎月第3会社営業日 9時」とする。") == ["E3060"]
    assert codes("実行の予定は、100とする。") == ["E3055"]
    assert codes("実行の時刻は、「9時」とする。") == ["E2022"]
    assert codes("もし1が1ならば、\n  実行の予定は、「毎日 9時」とする。\nここまで。") == ["E3054"]
    assert compile_source(Source("100を表示する。")).schedule is None


def test_next_run_with_company_calendar(tmp_path):
    (tmp_path / "会社休日.csv").write_text("日付\n2026/11/02\n", encoding="utf-8")
    code = "暦の会社休日は「会社休日.csv」とする。\n実行の予定は、「毎月第3会社営業日 9時」とする。"
    result = compile_source(Source(code), base_dir=tmp_path)
    assert result.errors == []
    assert api.next_run(result, at(2026, 11, 1), tmp_path) == at(2026, 11, 6, 9)


def test_next_run_beyond_holiday_data():
    result = compile_source(Source("実行の予定は、「毎営業日 9時」とする。"))
    with pytest.raises(api.ScheduleError) as e:
        api.next_run(result, at(2027, 12, 31, 10))
    assert "祝日データ" in str(e.value)
    assert api.next_run(compile_source(Source("100を表示する。")), at(2026, 1, 1)) is None


def test_catalog_example():
    settings = {(s["namespace"], s["key"]): s["example"] for s in api.catalog()["settings"]}
    assert settings[("実行", "予定")] == "「毎月25日 9時」"
