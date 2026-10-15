"""运行时的值和算术

数只有两种表示：整数用 int，小数用 Decimal，绝不用 float（语法设计决定记录）。
每次计算后都规整一次：结果是整数就变回 int，所以 1.5×2 显示为 3 而不是 3.0。

金額（Money）与数宽松互通（语法设计 12.4）：有一方是金額，结果就是金額；
只有 金額×金額、数÷金額 没有意义（意味解析会先报错，这里是最后一道防线）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from dandori import dates

from dandori.money import Money
from dandori.plugin.registry import VerbError
from dandori.table.interface import BLANK, Column, Table
from dandori.text import display_width, pad
from dandori.types import Type

Number = int | Decimal
Value = Number | Money | str | list | Table


class FileName(str):
    """带表名的文件名：「請求一覧.xlsx」の「10月」シート（语法设计 19）

    本身就是文件名（权限、路径的检查照旧），另外带着表名。
    """

    sheet: str | None

    def __new__(cls, name: str, sheet: str | None = None):
        obj = super().__new__(cls, name)
        obj.sheet = sheet
        return obj


def canonical(n: Number) -> Number:
    if isinstance(n, Decimal) and n == n.to_integral_value():
        return int(n)
    return n


def _amount(v) -> Number:
    return v.amount if isinstance(v, Money) else v


def _wrap(result: Number, money: bool):
    return Money(result) if money else canonical(result)


def _days(n) -> timedelta:
    if isinstance(n, Decimal):
        raise VerbError("E5011", value=format_value(n))
    return timedelta(days=n)


def _shift(d: date, offset, sign: int, calendar_for):
    """日付 ± 数（天数）・営業日数・月数（语法设计 13.3、14.4、14.5）

    calendar_for：日历的种类（「銀行」「会社」）→ 营业日历
    """
    if isinstance(offset, dates.BusinessDays):
        return calendar_for(offset.calendar).shift(d, sign * offset.count)
    if isinstance(offset, dates.Months):
        return dates.add_months(d, sign * offset.count)
    return d + sign * _days(offset)


def add(a, b, calendar_for=None):
    # 日付 + 数：数是天数（语法设计 13.3）
    if isinstance(a, date):
        return _shift(a, b, 1, calendar_for)
    if isinstance(b, date):
        return _shift(b, a, 1, calendar_for)
    return _wrap(_amount(a) + _amount(b), isinstance(a, Money) or isinstance(b, Money))


def subtract(a, b, calendar_for=None):
    # 日付 − 日付 = 天数；日付 − 数 = 日付
    if isinstance(a, date):
        return (a - b).days if isinstance(b, date) else _shift(a, b, -1, calendar_for)
    return _wrap(_amount(a) - _amount(b), isinstance(a, Money) or isinstance(b, Money))


def multiply(a, b):
    if isinstance(a, Money) and isinstance(b, Money):
        raise VerbError("E3043")
    return _wrap(_amount(a) * _amount(b), isinstance(a, Money) or isinstance(b, Money))


def divide(a, b):
    """除法：除不尽时得到 Decimal（精度 28 位）。金額÷金額 是比例（数）

    端数处理（切り捨て、四捨五入等）由用户明确写出（语法设计 12.5）。
    """
    if _amount(b) == 0:
        raise VerbError("E5001")
    if isinstance(b, Money) and not isinstance(a, Money):
        raise VerbError("E3044")
    result = Decimal(_amount(a)) / Decimal(_amount(b))
    return _wrap(result, isinstance(a, Money) and not isinstance(b, Money))


def check_no_fraction(value) -> None:
    """显示、写出之前：金額不能带 1 円以下的零头（语法设计 12.5）"""
    if isinstance(value, Money) and value.has_fraction:
        raise VerbError("E5010", value=value.format())
    if isinstance(value, Table):
        for row in value.rows:
            for cell in row:
                check_no_fraction(cell)
    elif isinstance(value, list):
        for item in value:
            check_no_fraction(item)


def check_no_blanks(numbers: list) -> None:
    """要计算的值里有空欄时报错，并指出它来自哪个文件的第几行（语法设计 8.7）"""
    for i, n in enumerate(numbers):
        if n is BLANK:
            where = numbers.origin(i) if isinstance(numbers, Column) else "並びの中"
            raise VerbError("E5004", where=where)


def format_value(value: Value) -> str:
    """显示用的文字"""
    if isinstance(value, Table):
        return format_table(value)
    if value is BLANK:
        return "（空欄）"
    if isinstance(value, list):
        return "、".join(format_value(v) for v in value)
    if isinstance(value, Money):
        return value.format()
    if isinstance(value, date):
        return dates.format_western(value)        # 西暦（语法设计 13.6）
    if isinstance(value, (dates.BusinessDays, dates.Months)):
        return value.format()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def format_table(table: Table) -> str:
    """按列对齐显示：全角字符占两格，数右对齐，空欄显示为空白"""
    header = table.display_names
    cells = [["" if v is BLANK else format_value(v) for v in row] for row in table.rows]
    numeric = [t in (Type.NUMBER, Type.MONEY) for _, t in table.type.columns]
    widths = [max([display_width(h)] + [display_width(row[i]) for row in cells])
              for i, h in enumerate(header)]
    lines = ["  ".join(pad(h, w, right=num) for h, w, num in zip(header, widths, numeric)).rstrip()]
    for row in cells:
        lines.append("  ".join(pad(c, w, right=num) for c, w, num in zip(row, widths, numeric)).rstrip())
    return "\n".join(lines)
