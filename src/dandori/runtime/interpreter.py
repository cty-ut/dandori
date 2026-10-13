"""解释器：执行对好号的程序

阶段 1、2 直接遍历语法树执行（架构设计 10.3），阶段 3 起改为执行计划。
做法很朴素：一句一句、一个手順一个手順地做，
每一步先把参数的值算出来，再调用动词的实现函数。
もし文就是先依次检查条件，执行第一个成立的分支。
繰り返し文在开始时把次数、范围或要处理的行确定好，之后不再改变，所以一定会结束。
「繰り返しをやめる」「次に進む」用异常实现：从循环体的深处直接跳回最内层的循环。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from dandori import dates
from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.analyzer import (BoundAssignment, BoundCountLoop, BoundEachLoop, BoundIf,
                                      BoundProgram, BoundRangeLoop, BoundSetting, BoundStatement)
from dandori.plugin.registry import ParamKind, VerbError
from dandori.runtime import values
from dandori.runtime.transaction import Transaction
from dandori.table.interface import BLANK, Row, Table
from dandori.types import TableType


@dataclass
class Context:
    """执行上下文：程序与外部世界的唯一接口（架构设计 7.3）

    「表示する」不直接 print，而是交给 ctx.output，
    这样测试时可以把输出收集起来，以后也可以换成写日志、发到网页等。
    """
    output: Callable[[str], None] = field(default=print)
    base_dir: Path = field(default_factory=Path.cwd)   # 程序文件所在的目录：相对路径以它为准
    # 写出的内容先暂存在这里，程序全部成功后才写入（语法设计 11.4）
    transaction: Transaction = field(default_factory=Transaction)
    dry_run: bool = False                              # 「試し」：执行全部，但不写入
    # 「今日」：程序中唯一随时间变化的值，由执行上下文提供（语法设计 13.7）。固定日本时间
    today: date = field(default_factory=dates.today_jst)
    # 注册表：日付的属性、营业日日历都由它提供（api.run 设置；直接调用 execute 时使用标准的注册表）
    registry: object = None
    # 设定文：(名前空间, 设定名) → 值（语法设计 16.2）。执行到設定文时登记
    settings: dict = field(default_factory=dict)
    # 权限声明：[(种类, 路径, 是否文件夹)]；None 表示没有声明（语法设计 17.1）
    permissions: tuple | None = None
    # 执行中读取的文件：(文件名, 行数, SHA-256)。执行日志用（语法设计 17.2）
    reads: list = field(default_factory=list)

    def record_read(self, file_name: str, path: Path, rows: int) -> None:
        import hashlib
        self.reads.append((file_name, rows, hashlib.sha256(path.read_bytes()).hexdigest()))

    @property
    def calendar(self):
        return self.registry.calendar if self.registry is not None else None

    def calendar_for(self, kind: str):
        """「銀行」→ 银行营业日历；「会社」→ 根据「暦の会社休日」生成的公司日历（只生成一次）"""
        if kind == "銀行":
            return self.calendar
        cache = self.__dict__.setdefault("_calendars", {})
        if kind not in cache:
            cache[kind] = self.registry.calendar_factories[kind](self)
        return cache[kind]


class ExecutionError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


class _Break(Exception):
    """繰り返しをやめる"""


class _Continue(Exception):
    """次に進む"""


def execute(program: BoundProgram, ctx: Context) -> None:
    if ctx.registry is None:
        from dandori.plugin.registry import default_registry
        ctx.registry = default_registry()
    ctx.permissions = program.permissions
    _Executor(ctx).block(program.sentences, last=None)


class _Executor:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.env: dict[str, values.Value] = {}   # 变量：名字 → 值
        self.rows: list[Row] = []                # 逐行处理中的当前行（最内层在最后）

    def block(self, statements: tuple[BoundStatement, ...], last):
        """执行一串文。last 是最近一个手順的结果：「それ」和自动填入都指向它"""
        for statement in statements:
            last = self.statement(statement, last)
        return last

    def statement(self, statement: BoundStatement, last):
        if isinstance(statement, BoundAssignment):
            self.env[statement.name] = self.evaluate(statement.value, last)
            return None                          # 赋值不产生结果

        if isinstance(statement, BoundSetting):
            self.ctx.settings[(statement.namespace, statement.key)] = statement.value
            return None

        if isinstance(statement, ast.Break):
            raise _Break
        if isinstance(statement, ast.Continue):
            raise _Continue

        if isinstance(statement, BoundCountLoop):
            count = self.evaluate(statement.count, last)
            if not isinstance(count, int) or count < 0:
                self._fail("E5002", statement.count.span, value=values.format_value(count))
            for _ in range(count):
                if not self.loop_body(statement.body):
                    break
            return None

        if isinstance(statement, BoundEachLoop):
            table = self.evaluate(statement.table, last)
            for row in list(table.records()):            # 开始时就确定了要处理的行
                self.rows.append(row)
                try:
                    if not self.loop_body(statement.body):
                        break
                finally:
                    self.rows.pop()
            return None

        if isinstance(statement, BoundRangeLoop):
            first = self.evaluate(statement.start, last)
            final = self.evaluate(statement.end, last)
            for expr, value in ((statement.start, first), (statement.end, final)):
                if not isinstance(value, int):
                    self._fail("E5003", expr.span, value=values.format_value(value))
            for i in range(first, final + 1):          # 两端都包含；开始 > 结束时执行 0 次
                self.env[statement.var] = i
                if not self.loop_body(statement.body):
                    break
            self.env.pop(statement.var, None)          # 循环变量只在循环内有效
            return None

        if isinstance(statement, BoundIf):
            for branch in statement.branches:
                if branch.condition is None or self.check(branch.condition, last):
                    self.block(branch.body, last=None)   # 「それ」不跨越块的边界
                    break
            return None

        for step in statement.steps:
            params = {p.name: p for p in step.spec.params}
            args = {name: self.argument(params[name].kind, expr, last) for name, expr in step.args.items()}
            try:
                last = step.spec.impl(self.ctx, **args)
            except VerbError as e:
                params = dict(e.params)
                if e.code == "E5004" and params.get("where") == "並びの中" and self.rows:
                    params["where"] = self.rows[-1].origin   # 逐行处理中：空欄多半来自当前行
                raise ExecutionError(Diagnostic.make(e.code, step.span, **params)) from None
            if isinstance(step.result_type, TableType) and isinstance(last, Table) \
                    and last.type != step.result_type:
                # 编译时读取的文件，执行时列定义变了（两次读取之间被修改）
                self._fail("E5005", step.span, file=args.get("path", ""),
                           reason="列が確認したときと変わっています")
        if statement.bind is not None:
            self.env[statement.bind] = last
            return None
        return last

    def loop_body(self, body) -> bool:
        """执行一轮循环体。返回 False 表示「繰り返しをやめる」，循环应当结束"""
        try:
            self.block(body, last=None)                  # 「それ」不跨越块的边界
        except _Continue:
            pass
        except _Break:
            return False
        return True

    @staticmethod
    def _fail(code: str, span, **params):
        raise ExecutionError(Diagnostic.make(code, span, **params))

    # ---------- 参数：按种类准备好传给动词的东西（语法设计 9.9） ----------

    def argument(self, kind: ParamKind, expr: ast.Expr, last):
        match kind:
            case ParamKind.VALUE:
                return self.evaluate(expr, last)
            case ParamKind.CONDITION:
                return lambda row: self.test(expr.condition, last, row)
            case ParamKind.COLUMNS:
                items = expr.items if isinstance(expr, ast.ListExpr) else (expr,)
                return [ast.column_name(item) for item in items]
            case ParamKind.SORT:
                return ast.column_name(expr.target), expr.descending
            case ParamKind.COLUMN:
                return self.evaluate(expr.table, last), expr.column
            case ParamKind.BLANKS:
                return self.evaluate(expr.target.table, last), expr.target.column
        raise TypeError(kind)

    # ---------- 条件 ----------

    def check(self, condition: ast.Condition, last) -> bool:
        """もし文的条件：比较出错（如逐行处理中当前行是空欄）时，报告条件的位置"""
        try:
            return self.test(condition, last)
        except VerbError as e:
            raise ExecutionError(Diagnostic.make(e.code, condition.span, **e.params)) from None

    def test(self, condition: ast.Condition, last, row: Row | None = None) -> bool:
        """row 不为 None 时，条件中的列名取这一行的值（「〜のもの」）"""
        if isinstance(condition, ast.Logical):
            # 生成器 + all/any：从左往右检查，结果确定了就停止。
            # 所以「金額が空欄でないかつ金額が10000以上」不会对空欄做大小比较
            results = (self.test(item, last, row) for item in condition.items)
            return all(results) if condition.op == "かつ" else any(results)
        if isinstance(condition.right, ast.DayKind):
            return self._day_kind(condition, last, row)
        a = self.evaluate(condition.left, last, row)
        b = self.evaluate(condition.right, last, row)
        if (a is BLANK or b is BLANK) and condition.op not in ("=", "<>"):
            side = condition.left if a is BLANK else condition.right
            column = side.name if isinstance(side, ast.Name) else "値"
            raise VerbError("E5006", column=column, where=self._origin(side, row))
        return _COMPARISONS[condition.op](a, b)

    def _day_kind(self, condition: ast.Comparison, last, row) -> bool:
        """期日が休日（でない）、期日が営業日（语法设计 14.4）"""
        day = self.evaluate(condition.left, last, row)
        if day is BLANK:
            column = condition.left.name if isinstance(condition.left, ast.Name) else "値"
            raise VerbError("E5006", column=column, where=self._origin(condition.left, row))
        kind = condition.right.kind
        calendar = self.ctx.calendar_for("会社" if kind.startswith("会社") else "銀行")
        business = calendar.is_business_day(day)
        result = business if kind.endswith("営業日") else not business
        return result if condition.op == "=" else not result

    # ---------- 值 ----------

    def evaluate(self, expr: ast.Expr, last, row: Row | None = None):
        match expr:
            case ast.Number(value=value) | ast.String(value=value) | ast.DateLiteral(value=value):
                return value
            case ast.Today():
                return self.ctx.today
            case ast.Blank():
                return BLANK
            case ast.Sore() | ast.PreviousResult():
                return last
            case ast.Name(name=name):
                source = self._row_with(name, row)
                if source is not None:
                    return source.values[name]     # 条件、逐行处理中的列名：这一行的值
                return self.env[name]
            case ast.ColumnRef(table=table, column=column):
                target = self.evaluate(table, last, row)
                if isinstance(target, date) or (isinstance(target, list) and not isinstance(target, Table)):
                    function = self.ctx.registry.date_property(column).function
                    try:
                        return _date_property(target, function)      # 期日の月末（语法设计 13.5）
                    except VerbError as e:
                        raise ExecutionError(Diagnostic.make(e.code, expr.span, **e.params)) from None
                return target.column(column)
            case ast.ListExpr(items=items):
                return [self.evaluate(item, last, row) for item in items]
            case ast.Negate(operand=operand):
                a = self.evaluate(operand, last, row)
                self._no_blank(a, operand, row)
                return values.subtract(0, a)
            case ast.BinaryOp(op=op, left=left, right=right):
                a, b = self.evaluate(left, last, row), self.evaluate(right, last, row)
                self._no_blank(a, left, row)
                self._no_blank(b, right, row)
                try:
                    if op in ("+", "-"):
                        return _OPERATORS[op](a, b, self.ctx.calendar_for)   # 日付 ± 営業日数 需要日历
                    return _OPERATORS[op](a, b)
                except VerbError as e:
                    raise ExecutionError(Diagnostic.make(e.code, expr.span, **e.params)) from None
        raise TypeError(f"未知のノード：{expr!r}")


    # ---------- 当前行 ----------

    def _row_with(self, name: str, row: Row | None) -> Row | None:
        """名字是哪一行的列：先看条件正在检查的行，再从内到外看逐行处理的当前行"""
        for candidate in ([row] if row is not None else []) + self.rows[::-1]:
            if name in candidate.values:
                return candidate
        return None

    def _origin(self, expr: ast.Expr, row: Row | None) -> str:
        if isinstance(expr, ast.Name) and (source := self._row_with(expr.name, row)) is not None:
            return source.origin
        return ""

    def _no_blank(self, value, expr: ast.Expr, row: Row | None) -> None:
        """当前行的空欄参与计算：报错，指出是哪个文件的第几行"""
        if value is BLANK:
            raise ExecutionError(Diagnostic.make("E5004", expr.span, where=self._origin(expr, row)))


def _date_property(target, function):
    if isinstance(target, list):
        return [v if v is BLANK else function(v) for v in target]
    return function(target)


_OPERATORS = {
    "+": values.add,
    "-": values.subtract,
    "*": values.multiply,
    "/": values.divide,
}

_COMPARISONS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "=": lambda a, b: a == b,
    "<>": lambda a, b: a != b,
}
