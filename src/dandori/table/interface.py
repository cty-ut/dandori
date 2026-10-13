"""表格的实现（架构设计 5.3）

用纯 Python 实现，不用 pandas、Polars：它们默认使用浮点数，与"金额不能有浮点误差"冲突。
一张表 = 列定义 + 若干行。每一行记住自己来自哪个文件的第几行，报错时可以指出位置。

插件和用户程序只通过这里的接口使用表格，以后可以在后面换成更快的实现。
"""

from __future__ import annotations

from dandori.types import TableType


class _Blank:
    """空欄：空格子。不是 0，也不是空字符串（设计原则 9：不要隐式的 NULL）"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "空欄"


BLANK = _Blank()


class Column(list):
    """一列的值。就是一个普通的列表（所以「足す」等动词可以直接用），外加每个值的来源"""

    def __init__(self, values, origins: list[str]):
        super().__init__(values)
        self.origins = origins      # 如「明細.csv の 4行目」

    def origin(self, index: int) -> str:
        return self.origins[index]


class Table:
    def __init__(self, table_type: TableType, rows: list[tuple], origins: list[str],
                 display_names: list[str]):
        self.type = table_type
        self.rows = rows                    # 每一行是一个 tuple，顺序与列定义相同
        self.origins = origins              # 每一行的来源
        self.display_names = display_names  # 显示用的列名（原文，未正規化）

    def column(self, name: str) -> Column:
        index = self.type.names.index(name)
        return Column([row[index] for row in self.rows], self.origins)

    def __len__(self) -> int:
        return len(self.rows)
