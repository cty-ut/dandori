"""実行の予定（平台设计.md 第 3 节）：実行の予定は、「毎月25日 9時」とする。

语言只负责检查写法、计算"下一次是什么时候"、在「説明」中显示；按时运行的是平台。
手动「dandori 実行」时，这个设定没有作用。

写法：〈日〉［（休日なら前営業日｜翌営業日）］〈時刻〉

    毎日 / 毎営業日 / 毎会社営業日
    毎週月曜日
    毎月25日 / 毎月末日
    毎月第3営業日 / 毎月最終営業日（「会社営業日」も可）
    時刻：9時、9時30分、9時半、9:30（24時間制）

- 时刻必须写：几点运行是业务的决定，不由语言给默认值
- 毎月29日〜31日 报错：有的月份没有这一天，悄悄跳过比报错更糟。用「毎月末日」
- 不写「休日なら〜」时，即使是休日也在那天运行（"毎月25日"的字面意思）
"""

from __future__ import annotations

import re
import unicodedata
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from dandori.dates import JST
from dandori.plugin.registry import VerbError

WEEKDAYS = "月火水木金土日"

_DAY = re.compile(
    r"毎日"
    r"|毎(?P<biz_co>会社)?営業日"
    r"|毎週(?P<weekday>[月火水木金土日])曜日"
    r"|毎月(?P<day>\d+)日"
    r"|毎月(?P<month_end>末日)"
    r"|毎月第(?P<nth>\d+)(?P<nth_co>会社)?営業日"
    r"|毎月最終(?P<last_co>会社)?営業日"
)
_SHIFT = re.compile(r"\(休日なら(?P<dir>前|翌)(?P<co>会社)?営業日\)")
_TIME = re.compile(r"(?P<h>\d+)時(?:(?P<m>\d+)分|(?P<half>半))?|(?P<h2>\d+):(?P<m2>\d+)")


@dataclass(frozen=True)
class Schedule:
    text: str                  # 程序中写的原文
    kind: str                  # daily / business / weekly / day / month_end / nth / last
    at: time
    company: bool = False      # 营业日按公司日历
    weekday: int = 0           # weekly：0 = 月曜日
    day: int = 0               # day：几号
    nth: int = 0               # nth：第几个营业日
    shift: str | None = None   # 休日のとき："前" / "翌"
    shift_company: bool = False

    @property
    def uses_calendar(self) -> bool:
        return self.kind in ("business", "nth", "last") or self.shift is not None

    @property
    def uses_company(self) -> bool:
        return self.company or self.shift_company

    def describe(self) -> str:
        """给人看的说明（「説明」、平台的一覧）"""
        biz = "会社営業日" if self.company else "営業日"
        day = {
            "daily": "毎日",
            "business": f"毎{biz}",
            "weekly": f"毎週{WEEKDAYS[self.weekday]}曜日",
            "day": f"毎月{self.day}日",
            "month_end": "毎月末日",
            "nth": f"毎月第{self.nth}{biz}",
            "last": f"毎月最終{biz}",
        }[self.kind]
        text = f"{day} {self.at.hour}時{self.at.minute:02d}分"
        if self.shift:
            text += f"（休日なら{self.shift}{'会社' if self.shift_company else ''}営業日）"
        elif self.kind in ("weekly", "day", "month_end"):
            text += "（休日でも実行します）"
        return text

    def next_after(self, after: datetime, bank, company=None) -> datetime:
        """after 之后（不含）的第一次运行时间（JST）。bank、company 是营业日历"""
        after = after.astimezone(JST)
        for day in self._days(after.date() - timedelta(days=7), bank, company):
            moment = datetime.combine(day, self.at, JST)
            if moment > after:
                return moment
        raise AssertionError("unreachable")

    # ---------- 内部 ----------

    def _days(self, start: date, bank, company):
        """从 start 附近开始，按时间顺序产生运行的日子"""
        calendar = company if self.company else bank
        shift_calendar = company if self.shift_company else bank
        if self.kind in ("daily", "business"):
            day = start
            while True:
                if self.kind == "daily" or calendar.is_business_day(day):
                    yield day
                day += timedelta(days=1)
        if self.kind == "weekly":
            day = start + timedelta(days=(self.weekday - start.weekday()) % 7)
            while True:
                yield self._shift(day, shift_calendar)
                day += timedelta(days=7)
        year, month = start.year, start.month
        while True:
            yield self._month_day(year, month, calendar, shift_calendar)
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    def _month_day(self, year: int, month: int, calendar, shift_calendar) -> date:
        last = date(year, month, monthrange(year, month)[1])
        if self.kind == "day":
            return self._shift(date(year, month, self.day), shift_calendar)
        if self.kind == "month_end":
            return self._shift(last, shift_calendar)
        if self.kind == "last":
            return last if calendar.is_business_day(last) else calendar.previous(last)
        first = date(year, month, 1)
        start = first if calendar.is_business_day(first) else calendar.next(first)
        return calendar.shift(start, self.nth - 1) if self.nth > 1 else start

    def _shift(self, day: date, calendar) -> date:
        if self.shift == "前":
            return calendar.backward(day)
        if self.shift == "翌":
            return calendar.forward(day)
        return day


def parse_schedule(text: str) -> Schedule:
    """写法不对时抛出 VerbError"""
    normalized = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    day = _DAY.match(normalized)
    if day is None:
        raise VerbError("E3062", text=text)
    if day["day"] and int(day["day"]) > 28:          # 先报日子的问题，再报时刻
        raise VerbError("E3064", day=str(int(day["day"])))
    rest = normalized[day.end():]
    shift = _SHIFT.match(rest)
    if shift:
        rest = rest[shift.end():]
    at = _TIME.fullmatch(rest)
    if at is None:
        if rest == "":
            raise VerbError("E3063", text=text)
        raise VerbError("E3062", text=text)
    hour, minute = int(at["h"] or at["h2"]), 30 if at["half"] else int(at["m"] or at["m2"] or 0)
    if hour > 23 or minute > 59:
        raise VerbError("E3062", text=text)

    fields: dict = {"text": text, "at": time(hour, minute)}
    if day["weekday"]:
        fields.update(kind="weekly", weekday=WEEKDAYS.index(day["weekday"]))
    elif day["day"]:
        n = int(day["day"])
        if n < 1:
            raise VerbError("E3062", text=text)
        fields.update(kind="day", day=n)
    elif day["month_end"]:
        fields.update(kind="month_end")
    elif day["nth"]:
        n = int(day["nth"])
        if not 1 <= n <= 15:
            raise VerbError("E3062", text=text)
        fields.update(kind="nth", nth=n, company=bool(day["nth_co"]))
    elif day.group(0).startswith("毎月最終"):
        fields.update(kind="last", company=bool(day["last_co"]))
    elif day.group(0) == "毎日":
        fields.update(kind="daily")
    else:
        fields.update(kind="business", company=bool(day["biz_co"]))

    if shift:
        if fields["kind"] not in ("weekly", "day", "month_end"):
            raise VerbError("E3065", text=text)
        fields.update(shift=shift["dir"], shift_company=bool(shift["co"]))
    return Schedule(**fields)
