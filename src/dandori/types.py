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

LIST_OF = {Type.NUMBER: Type.NUMBER_LIST, Type.STRING: Type.STRING_LIST}
