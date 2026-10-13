"""日付（语法设计第 13 节）

日本的日期有几个特有的问题，都在这里处理：
- 和暦：元号（令和、平成…）与西暦的互换；「元年」；改元前后不存在的日期（平成32年）
- 年度：4 月开始
- 今日：固定使用日本时间（JST）。云服务器的 UTC 在日本时间 9 点之前还是前一天
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))     # 日本没有夏令时，固定 UTC+9 即可


@dataclass(frozen=True)
class Era:
    name: str
    letter: str      # R8.10.31 中的 R
    start: date      # 该元号的第一天


# 按开始日期从新到旧排列
ERAS = (
    Era("令和", "R", date(2019, 5, 1)),
    Era("平成", "H", date(1989, 1, 8)),
    Era("昭和", "S", date(1926, 12, 25)),
    Era("大正", "T", date(1912, 7, 30)),
    Era("明治", "M", date(1868, 10, 23)),
)
ERA_BY_NAME = {e.name: e for e in ERAS}
ERA_BY_LETTER = {e.letter: e for e in ERAS}
WEEKDAYS = "月火水木金土日"


class InvalidDate(ValueError):
    """不存在的日期。hint 是给用户的修改建议（可能为空）"""

    def __init__(self, hint: str = ""):
        super().__init__(hint)
        self.hint = hint


def make_date(year: int, month: int, day: int, era: Era | None = None) -> date:
    """年月日 → date。era 不为 None 时 year 是和暦的年。不存在时抛出 InvalidDate"""
    western = era.start.year + year - 1 if era else year
    try:
        result = date(western, month, day)
    except ValueError:
        raise InvalidDate() from None
    if era is not None:
        actual = era_of(result)
        if actual is None or actual[0] is not era:
            # 平成32年1月1日 → 令和2年1月1日；令和元年4月1日 → 平成31年4月1日
            hint = f"「{format_wareki(result)}」のことですか？" if actual else ""
            raise InvalidDate(hint)
    return result


def era_of(d: date) -> tuple[Era, int] | None:
    """date → (元号, 和暦的年)"""
    for era in ERAS:
        if d >= era.start:
            return era, d.year - era.start.year + 1
    return None


# ---------- 显示 ----------

def format_western(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def format_wareki(d: date) -> str:
    found = era_of(d)
    if found is None:
        return format_western(d)
    era, year = found
    return f"{era.name}{'元' if year == 1 else year}年{d.month}月{d.day}日"


def format_csv(d: date) -> str:
    """写出 CSV：Excel 识别为日期"""
    return f"{d.year}/{d.month:02d}/{d.day:02d}"


# ---------- 属性（期日の月末 等，语法设计 13.5） ----------

def month_start(d: date) -> date:
    return d.replace(day=1)


def month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def fiscal_year(d: date) -> int:
    """年度：4 月开始。2027年3月31日 是 2026年度"""
    return d.year if d.month >= 4 else d.year - 1


PROPERTIES = {
    # 名字: (求值函数, 结果的类型名)
    "年": (lambda d: d.year, "数"),
    "月": (lambda d: d.month, "数"),
    "日": (lambda d: d.day, "数"),
    "曜日": (lambda d: WEEKDAYS[d.weekday()], "文字列"),
    "月初": (month_start, "日付"),
    "月末": (month_end, "日付"),
    "年度": (fiscal_year, "数"),
    "和暦": (format_wareki, "文字列"),
}


def today_jst() -> date:
    return datetime.now(JST).date()


# ---------- 解析 ----------

_ERA_NAMES = "|".join(ERA_BY_NAME)
# 程序中的写法（语法设计 13.2）：只认「年月日」
CODE_WESTERN = re.compile(r"(\d{1,4})年(\d{1,2})月(\d{1,2})日")
CODE_ERA = re.compile(rf"({_ERA_NAMES})(\d{{1,2}}|元)年(\d{{1,2}})月(\d{{1,2}})日")

# CSV 中的写法（语法设计 13.8）：外部数据，各种写法都认
_CELL_WESTERN = re.compile(r"(\d{4})(?:([/\-.])(\d{1,2})\2(\d{1,2})|年(\d{1,2})月(\d{1,2})日)")
_CELL_ERA_NAME = re.compile(rf"({_ERA_NAMES})(\d{{1,2}}|元)年(\d{{1,2}})月(\d{{1,2}})日")
_CELL_ERA_LETTER = re.compile(r"([MTSHRmtshr])(\d{1,2})([/.\-])(\d{1,2})\3(\d{1,2})")


def from_code(match: re.Match, era_match: bool) -> date:
    """程序中的日期字面量 → date（不存在时抛出 InvalidDate）"""
    if era_match:
        era_name, year, month, day = match.groups()
        return make_date(1 if year == "元" else int(year), int(month), int(day), ERA_BY_NAME[era_name])
    year, month, day = match.groups()
    return make_date(int(year), int(month), int(day))


def parse_cell(text: str) -> date | None:
    """CSV 的一个格子 → date。不像日期时返回 None；像日期但不存在时抛出 InvalidDate"""
    if m := _CELL_WESTERN.fullmatch(text):
        year = int(m.group(1))
        month, day = (m.group(3), m.group(4)) if m.group(2) else (m.group(5), m.group(6))
        return make_date(year, int(month), int(day))
    if m := _CELL_ERA_NAME.fullmatch(text):
        era_name, year, month, day = m.groups()
        return make_date(1 if year == "元" else int(year), int(month), int(day), ERA_BY_NAME[era_name])
    if m := _CELL_ERA_LETTER.fullmatch(text):
        letter, year, _, month, day = m.groups()
        return make_date(int(year), int(month), int(day), ERA_BY_LETTER[letter.upper()])
    return None
