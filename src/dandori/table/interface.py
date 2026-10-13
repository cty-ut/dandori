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


class Row:
    """表的一行：列名 → 值，以及来源（「明細.csv の 4行目」）。条件对每一行求值时使用"""

    def __init__(self, values: dict, origin: str):
        self.values = values
        self.origin = origin


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

    # ---------- 表格操作的基本部件（语法设计 9.2）。全部返回新表，原表不变 ----------

    def records(self):
        names = self.type.names
        for row, origin in zip(self.rows, self.origins):
            yield Row(dict(zip(names, row)), origin)

    def _derive(self, rows, origins, table_type=None, display_names=None) -> Table:
        return Table(table_type or self.type, rows, origins, display_names or self.display_names)

    def filter(self, predicate) -> Table:
        kept = [(row, origin) for row, origin, record in zip(self.rows, self.origins, self.records())
                if predicate(record)]
        return self._derive([r for r, _ in kept], [o for _, o in kept])

    def select(self, names: list[str]) -> Table:
        indexes = [self.type.names.index(n) for n in names]
        table_type = TableType(tuple(self.type.columns[i] for i in indexes))
        rows = [tuple(row[i] for i in indexes) for row in self.rows]
        return self._derive(rows, self.origins, table_type, [self.display_names[i] for i in indexes])

    def sort(self, name: str, descending: bool) -> Table:
        """排序。空欄总是排在最后；相等的行保持原来的顺序（Python 的排序是稳定的）"""
        index = self.type.names.index(name)
        pairs = list(zip(self.rows, self.origins))
        filled = [p for p in pairs if p[0][index] is not BLANK]
        blanks = [p for p in pairs if p[0][index] is BLANK]
        filled.sort(key=lambda p: p[0][index], reverse=descending)
        ordered = filled + blanks
        return self._derive([r for r, _ in ordered], [o for _, o in ordered])

    def fill_blanks(self, name: str, value) -> Table:
        index = self.type.names.index(name)
        rows = [row[:index] + (value,) + row[index + 1:] if row[index] is BLANK else row
                for row in self.rows]
        return self._derive(rows, self.origins)

    def groups(self, keys: list[str]) -> list[tuple[tuple, list[int]]]:
        """按 keys 分组：[(键的值, [行号…])]，按各组第一次出现的顺序"""
        indexes = [self.type.names.index(k) for k in keys]
        groups: dict[tuple, list[int]] = {}
        for i, row in enumerate(self.rows):
            groups.setdefault(tuple(row[j] for j in indexes), []).append(i)
        return list(groups.items())

    def display_name(self, name: str) -> str:
        return self.display_names[self.type.names.index(name)]
