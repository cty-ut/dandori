"""运行时的值和算术

数只有两种表示：整数用 int，小数用 Decimal，绝不用 float（语法设计决定记录）。
每次计算后都规整一次：结果是整数就变回 int，所以 1.5×2 显示为 3 而不是 3.0。
"""

from __future__ import annotations

from decimal import Decimal

from dandori.plugin.registry import VerbError

Number = int | Decimal
Value = Number | str | list


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


def format_value(value: Value) -> str:
    """显示用的文字"""
    if isinstance(value, list):
        return "、".join(format_value(v) for v in value)
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)
