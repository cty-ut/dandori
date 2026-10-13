"""银行营业日（语法设计 14.2、14.3）

祝日不用公式计算，而是使用内閣府公布的祝日表（data/syukujitsu.csv）：
公式算不出临时祝日（2019 年即位前后的休日、2020/2021 年奥运会调整的祝日），
也算不出"国民の休日"（如 2026 年 9 月 22 日）。

日期超出祝日表的范围时报错，而不是悄悄当作"没有祝日"：
否则明年的程序会把元旦当成营业日。
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from importlib import resources

from dandori.dates import format_western
from dandori.plugin.registry import VerbError

# 银行法施行令：12 月 31 日 ～ 1 月 3 日是银行的休日
_BANK_NEW_YEAR = {(12, 31), (1, 1), (1, 2), (1, 3)}


class BankCalendar:
    def __init__(self, holidays: dict[date, str], source: str):
        self.holidays = holidays
        self.first_year = min(holidays).year
        self.last_year = max(holidays).year
        self.description = f"{source}（{self.first_year}年〜{self.last_year}年）"

    def _check(self, d: date) -> None:
        if not self.first_year <= d.year <= self.last_year:
            raise VerbError("E5012", first=str(self.first_year), last=str(self.last_year),
                            date=format_western(d))

    def is_business_day(self, d: date) -> bool:
        self._check(d)
        return (d.weekday() < 5 and d not in self.holidays
                and (d.month, d.day) not in _BANK_NEW_YEAR)

    def shift(self, d: date, n: int) -> date:
        """往后（n > 0）或往前（n < 0）数 n 个营业日，不含当天"""
        step = timedelta(days=1 if n > 0 else -1)
        remaining, current = abs(n), d
        while remaining:
            current += step
            if self.is_business_day(current):
                remaining -= 1
        return current

    def next(self, d: date) -> date:
        """翌営業日"""
        return self.shift(d, 1)

    def previous(self, d: date) -> date:
        """前営業日"""
        return self.shift(d, -1)

    def forward(self, d: date) -> date:
        """後倒し：是营业日就不变，是休日就推迟到下一个营业日"""
        return d if self.is_business_day(d) else self.next(d)

    def backward(self, d: date) -> date:
        """前倒し：是营业日就不变，是休日就提前到上一个营业日"""
        return d if self.is_business_day(d) else self.previous(d)


def load_bank_calendar() -> BankCalendar:
    text = resources.files("dandori.stdlib.japan").joinpath("data/syukujitsu.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(text.splitlines()))[1:]
    holidays = {}
    for day, name in rows:
        year, month, dd = (int(x) for x in day.split("/"))
        holidays[date(year, month, dd)] = name
    return BankCalendar(holidays, "内閣府「国民の祝日」")
