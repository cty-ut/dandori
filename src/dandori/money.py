"""金額（语法设计第 12 节）

日元金额。内部是精确值（int 或 Decimal），不用浮点数。
可以和不带单位的数直接比较、排序（宽松的规则：不带单位的数视为同一个单位）。
"""

from __future__ import annotations

import functools
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal


@functools.total_ordering
class Money:
    __slots__ = ("amount",)

    def __init__(self, amount: int | Decimal):
        if isinstance(amount, Decimal) and amount == amount.to_integral_value():
            amount = int(amount)
        self.amount = amount

    @property
    def has_fraction(self) -> bool:
        """有没有 1 円以下的零头"""
        return isinstance(self.amount, Decimal)

    # 比较：与金額比较，也与不带单位的数比较
    def __eq__(self, other) -> bool:
        if isinstance(other, Money):
            return self.amount == other.amount
        if isinstance(other, (int, Decimal)) and not isinstance(other, bool):
            return self.amount == other
        return NotImplemented

    def __lt__(self, other) -> bool:
        if isinstance(other, Money):
            return self.amount < other.amount
        if isinstance(other, (int, Decimal)):
            return self.amount < other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.amount)

    def __repr__(self) -> str:
        return f"Money({self.amount!r})"

    def format(self) -> str:
        """显示用：1,000円、-1,000円、零头时 1,101.1円"""
        if isinstance(self.amount, int):
            return f"{self.amount:,}円"
        return f"{self.amount.normalize():,f}円"


# 取整（语法设计 12.5）：与 Excel 的 ROUNDDOWN / ROUND / ROUNDUP 一致。
# 注意不能用 Python 的 round()：它是"银行家舍入"，round(2.5) == 2。
ROUNDING = {
    "切り捨てる": ROUND_DOWN,       # 向零：1.9 → 1，-1.9 → -1
    "四捨五入する": ROUND_HALF_UP,  # 真正的四舍五入：2.5 → 3，-2.5 → -3
    "切り上げる": ROUND_UP,         # 远离零：1.1 → 2，-1.1 → -2
}


def round_number(n: int | Decimal, mode: str) -> int:
    if isinstance(n, int):
        return n
    return int(n.quantize(Decimal(1), rounding=ROUNDING[mode]))
