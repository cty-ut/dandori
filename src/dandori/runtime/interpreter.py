"""解释器：执行对好号的程序

阶段 1、2 直接遍历语法树执行（架构设计 10.3），阶段 3 起改为执行计划。
做法很朴素：一句一句、一个手順一个手順地做，
每一步先把参数的值算出来，再调用动词的实现函数。
もし文就是先依次检查条件，执行第一个成立的分支。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.analyzer import BoundAssignment, BoundIf, BoundProgram, BoundStatement
from dandori.plugin.registry import VerbError
from dandori.runtime import values


@dataclass
class Context:
    """执行上下文：程序与外部世界的唯一接口（架构设计 7.3）

    「表示する」不直接 print，而是交给 ctx.output，
    这样测试时可以把输出收集起来，以后也可以换成写日志、发到网页等。
    """
    output: Callable[[str], None] = field(default=print)


class ExecutionError(Exception):
    def __init__(self, diagnostic: Diagnostic):
        super().__init__(diagnostic.message)
        self.diagnostic = diagnostic


def execute(program: BoundProgram, ctx: Context) -> None:
    _Executor(ctx).block(program.sentences, last=None)


class _Executor:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.env: dict[str, values.Value] = {}   # 变量：名字 → 值

    def block(self, statements: tuple[BoundStatement, ...], last):
        """执行一串文。last 是最近一个手順的结果：「それ」和自动填入都指向它"""
        for statement in statements:
            last = self.statement(statement, last)
        return last

    def statement(self, statement: BoundStatement, last):
        if isinstance(statement, BoundAssignment):
            self.env[statement.name] = self.evaluate(statement.value, last)
            return None                          # 赋值不产生结果

        if isinstance(statement, BoundIf):
            for branch in statement.branches:
                if branch.condition is None or self.test(branch.condition, last):
                    self.block(branch.body, last=None)   # 「それ」不跨越块的边界
                    break
            return None

        for step in statement.steps:
            args = {name: self.evaluate(expr, last) for name, expr in step.args.items()}
            try:
                last = step.spec.impl(self.ctx, **args)
            except VerbError as e:
                raise ExecutionError(Diagnostic.make(e.code, step.span, **e.params)) from None
        if statement.bind is not None:
            self.env[statement.bind] = last
            return None
        return last

    # ---------- 条件 ----------

    def test(self, condition: ast.Condition, last) -> bool:
        if isinstance(condition, ast.Logical):
            results = (self.test(item, last) for item in condition.items)
            return all(results) if condition.op == "かつ" else any(results)
        a = self.evaluate(condition.left, last)
        b = self.evaluate(condition.right, last)
        return _COMPARISONS[condition.op](a, b)

    # ---------- 值 ----------

    def evaluate(self, expr: ast.Expr, last):
        match expr:
            case ast.Number(value=value) | ast.String(value=value):
                return value
            case ast.Sore() | ast.PreviousResult():
                return last
            case ast.Name(name=name):
                return self.env[name]
            case ast.ListExpr(items=items):
                return [self.evaluate(item, last) for item in items]
            case ast.Negate(operand=operand):
                return values.subtract(0, self.evaluate(operand, last))
            case ast.BinaryOp(op=op, left=left, right=right):
                a, b = self.evaluate(left, last), self.evaluate(right, last)
                try:
                    return _OPERATORS[op](a, b)
                except VerbError as e:
                    raise ExecutionError(Diagnostic.make(e.code, expr.span, **e.params)) from None
        raise TypeError(f"未知のノード：{expr!r}")


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
