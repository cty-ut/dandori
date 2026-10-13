"""运行时的值和算术

数只有两种表示：整数用 int，小数用 Decimal，绝不用 float（语法设计决定记录）。
每次计算后都规整一次：结果是整数就变回 int，所以 1.5×2 显示为 3 而不是 3.0。
"""

from __future__ import annotations

from decimal import Decimal

from dandori.plugin.registry import VerbError
from dandori.table.interface import BLANK, Column, Table
from dandori.text import display_width, pad
from dandori.types import Type

Number = int | Decimal
Value = Number | str | list | Table


def canonical(n: Number) -> Number:
    if isinstance(n, Decimal) and n == n.to_integral_value():
        return int(n)
    return n


def add(a: Number, b: Number) -> Number:
    return canonical(a + b)


def subtract(a: Number, b: Number) -> Number:
    return canonical(a - b)


def multiply(a: Number, b: Number) -> Number:
    return canonical(a * b)


def divide(a: Number, b: Number) -> Number:
    """除法：除不尽时得到 Decimal（精度 28 位）

    10÷3 的端数处理（四舍五入、切り捨て等）属于业务规则，阶段 4 的「金額」类型再处理。
    """
    if b == 0:
        raise VerbError("E5001")
    return canonical(Decimal(a) / Decimal(b))


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
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)


def format_table(table: Table) -> str:
    """按列对齐显示：全角字符占两格，数右对齐，空欄显示为空白"""
    header = table.display_names
    cells = [["" if v is BLANK else format_value(v) for v in row] for row in table.rows]
    numeric = [t is Type.NUMBER for _, t in table.type.columns]
    widths = [max([display_width(h)] + [display_width(row[i]) for row in cells])
              for i, h in enumerate(header)]
    lines = ["  ".join(pad(h, w, right=num) for h, w, num in zip(header, widths, numeric)).rstrip()]
    for row in cells:
        lines.append("  ".join(pad(c, w, right=num) for c, w, num in zip(row, widths, numeric)).rstrip())
    return "\n".join(lines)
