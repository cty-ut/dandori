"""值的类型（语法设计 3.5、8.9）"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Type(Enum):
    NUMBER = "数"
    STRING = "文字列"
    NUMBER_LIST = "数の並び"      # 100と200、数的列
    STRING_LIST = "文字列の並び"  # 「a」と「b」、文字列的列
    LIST = "並び"                 # 混合的并列，如 1と「あ」
    MONEY = "金額"
    MONEY_LIST = "金額の並び"      # 100円と200円、金額的列
    DATE = "日付"
    DATE_LIST = "日付の並び"


@dataclass(frozen=True)
class TableType:
    """表的类型：列名和每列的类型。列定义不同的表是不同的类型"""

    columns: tuple[tuple[str, Type], ...]   # (正規化后的列名, 类型)

    @property
    def value(self) -> str:
        """与 Type 一样，用 .value 得到给人看的名字"""
        return f"表（列：{'、'.join(name for name, _ in self.columns)}）"

    def column(self, name: str) -> Type | None:
        return dict(self.columns).get(name)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.columns]


ValueType = Type | TableType

LIST_OF = {Type.NUMBER: Type.NUMBER_LIST, Type.STRING: Type.STRING_LIST, Type.MONEY: Type.MONEY_LIST,
           Type.DATE: Type.DATE_LIST}
ELEMENT_OF = {v: k for k, v in LIST_OF.items()}

# 可以参与计算、比较大小的类型（宽松的规则：金額与数互通，语法设计 12.4）
NUMERIC = frozenset({Type.NUMBER, Type.MONEY})
NUMERIC_LISTS = frozenset({Type.NUMBER_LIST, Type.MONEY_LIST})
