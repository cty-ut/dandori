"""表格操作（语法设计第 9 节）

对应关系代数：筛选（絞り込む）、取列（取り出す）、分组汇总（合計する・平均する・数える）、排序（並べる），
以及空欄的显式处理（埋める）。所有操作都生成新表，原表不变。

每个动词由三部分组成：
- 参数槽：用助词标记；条件、列名、排序的槽声明自己的列来自哪个槽（columns_of）
- infer：编译时推断结果的类型（例如有没有「ごとに」，结果分别是数和表）
- 实现函数：执行时真正做计算
"""

from __future__ import annotations

from dandori.frontend import ast
from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import Param, ParamKind as P, Registry, VerbError, VerbSpec
from dandori.runtime import values
from dandori.table.interface import Column, Table
from dandori.money import Money
from dandori.types import NUMERIC, NUMERIC_LISTS, TableType, Type

_TABLE = frozenset({TableType})
_NUMBERS = NUMERIC_LISTS                            # 数或金額的列
_SCALAR = NUMERIC | {Type.STRING, Type.DATE}


def column_names(expr: ast.Expr) -> list[str]:
    """「取引先と金額」→ ["取引先", "金額"]（意味解析已经确认它们都是列名）"""
    items = expr.items if isinstance(expr, ast.ListExpr) else (expr,)
    return [ast.column_name(item) for item in items]


def _table(t):
    return t if isinstance(t, TableType) else None


# ---------- 絞り込む：筛选 ----------

def shiborikomu(ctx, table: Table, condition):
    """明細から金額が10000以上のものを絞り込む"""
    return table.filter(condition)


def infer_same_table(args, types, ctx):
    return _table(types.get("table"))


# ---------- 取り出す：取列 ----------

def toridasu(ctx, table: Table, columns: list[str]):
    """明細から取引先と金額を取り出す"""
    return table.select(columns)


def infer_toridasu(args, types, ctx):
    table = _table(types.get("table"))
    if table is None:
        return None
    return TableType(tuple((n, table.column(n)) for n in column_names(args["columns"])))


# ---------- 合計する・平均する：汇总，可以分组 ----------

def _aggregate(column, keys, reduce_values, result_name=None):
    table, name = column
    value_type = table.type.column(name)           # 金額列的合计、平均也是金額
    if not keys:
        return reduce_values(table.column(name))

    rows, origins = [], []
    for key, indexes in table.groups(keys):
        group = Column([table.rows[i][table.type.names.index(name)] for i in indexes],
                       [table.origins[i] for i in indexes])
        rows.append(key + (reduce_values(group),))
        origins.append("、".join(f"{k}＝{values.format_value(v)}" for k, v in zip(keys, key)) + " の集計")
    columns = tuple((k, table.type.column(k)) for k in keys) + ((result_name or name, value_type),)
    display = [table.display_name(k) for k in keys] + [result_name or table.display_name(name)]
    return Table(TableType(columns), rows, origins, display)


def _sum(numbers):
    values.check_no_blanks(numbers)
    total = 0
    for n in numbers:
        total = values.add(total, n)
    return total


def _average(numbers):
    if not numbers:
        raise VerbError("E5007")
    return values.divide(_sum(numbers), len(numbers))


def goukei(ctx, column, keys=None):
    """明細の金額を合計する　／　明細の金額を取引先ごとに合計する"""
    return _aggregate(column, keys, _sum)


def heikin(ctx, column, keys=None):
    """明細の金額を平均する　／　明細の金額を取引先ごとに平均する"""
    return _aggregate(column, keys, _average)


def infer_aggregate(args, types, ctx):
    table = _table(types.get("column"))
    if table is None:
        return None
    name = args["column"].column
    value_type = table.column(name)                # 金額列的合计、平均也是金額
    if "keys" not in args:
        return value_type
    keys = column_names(args["keys"])
    return TableType(tuple((k, table.column(k)) for k in keys) + ((name, value_type),))


# ---------- 数える：计数，可以分组 ----------

def kazoeru(ctx, table: Table, keys=None):
    """明細を数える　／　明細を取引先ごとに数える"""
    if not keys:
        return len(table)
    rows, origins = [], []
    for key, indexes in table.groups(keys):
        rows.append(key + (len(indexes),))
        origins.append("、".join(f"{k}＝{values.format_value(v)}" for k, v in zip(keys, key)) + " の集計")
    columns = tuple((k, table.type.column(k)) for k in keys) + (("件数", Type.NUMBER),)
    return Table(TableType(columns), rows, origins, [table.display_name(k) for k in keys] + ["件数"])


def infer_kazoeru(args, types, ctx):
    if "keys" not in args:
        return Type.NUMBER
    table = _table(types.get("table"))
    if table is None:
        return None
    keys = column_names(args["keys"])
    return TableType(tuple((k, table.column(k)) for k in keys) + (("件数", Type.NUMBER),))


# ---------- 並べる：排序 ----------

def naraberu(ctx, table: Table, order):
    """明細を金額の大きい順に並べる"""
    name, descending = order
    return table.sort(name, descending)


# ---------- 埋める：把空欄填成指定的值 ----------

def umeru(ctx, target, value):
    """明細の金額の空欄を0で埋める"""
    table, name = target
    if table.type.column(name) is Type.MONEY and not isinstance(value, Money):
        value = Money(value)                       # 金額列的空欄用 0 填：填进去的是 0円
    return table.fill_blanks(name, value)


def infer_umeru(args, types, ctx):
    table = _table(types.get("target"))
    value = types.get("value")
    if table is None:
        return None
    column = args["target"].target.column
    column_type = table.column(column)
    compatible = value is column_type or (value in NUMERIC and column_type in NUMERIC)
    if value is not None and column_type is not None and not compatible:
        raise VerbError("E3034", column=column, column_type=column_type.value, actual=value.value)
    return table


def register(registry: Registry) -> None:
    def verb(id_, word, conjugation, params, impl, infer):
        registry.add_verb(VerbSpec(f"std.{id_}", word, conjugation, params,
                                   returns=None, impl=impl, infer=infer))

    verb("絞り込む", "絞り込む", Conjugation.GODAN,
         (Param("から", "table", _TABLE),
          Param("を", "condition", None, kind=P.CONDITION, columns_of="table")),
         shiborikomu, infer_same_table)
    verb("取り出す", "取り出す", Conjugation.GODAN,
         (Param("から", "table", _TABLE),
          Param("を", "columns", None, kind=P.COLUMNS, columns_of="table")),
         toridasu, infer_toridasu)
    verb("合計する", "合計する", Conjugation.SURU,
         (Param("を", "column", _NUMBERS, kind=P.COLUMN),
          Param("ごとに", "keys", None, required=False, kind=P.COLUMNS, columns_of="column")),
         goukei, infer_aggregate)
    verb("平均する", "平均する", Conjugation.SURU,
         (Param("を", "column", _NUMBERS, kind=P.COLUMN),
          Param("ごとに", "keys", None, required=False, kind=P.COLUMNS, columns_of="column")),
         heikin, infer_aggregate)
    verb("数える", "数える", Conjugation.ICHIDAN,
         (Param("を", "table", _TABLE),
          Param("ごとに", "keys", None, required=False, kind=P.COLUMNS, columns_of="table")),
         kazoeru, infer_kazoeru)
    verb("並べる", "並べる", Conjugation.ICHIDAN,
         (Param("を", "table", _TABLE),
          Param("に", "order", None, kind=P.SORT, columns_of="table")),
         naraberu, infer_same_table)
    verb("埋める", "埋める", Conjugation.ICHIDAN,
         (Param("を", "target", None, kind=P.BLANKS),
          Param("で", "value", _SCALAR)),
         umeru, infer_umeru)
