"""解释器：执行对好号的程序

阶段 1 直接遍历语法树执行（架构设计 10.3），阶段 3 起改为执行计划。
做法很朴素：一句一句、一个手順一个手順地做，
每一步先把参数的值算出来，再调用动词的实现函数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.analyzer import BoundAssignment, BoundProgram
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
    env: dict[str, values.Value] = {}   # 变量：名字 → 值
    last = None                          # 最近一个手順的结果：「それ」和自动填入都指向它

    for sentence in program.sentences:
        if isinstance(sentence, BoundAssignment):
            env[sentence.name] = _evaluate(sentence.value, last, env)
            last = None                  # 赋值不产生结果
            continue

        for step in sentence.steps:
            args = {name: _evaluate(expr, last, env) for name, expr in step.args.items()}
            try:
                last = step.spec.impl(ctx, **args)
            except VerbError as e:
                raise ExecutionError(Diagnostic.make(e.code, step.span, **e.params)) from None
        if sentence.bind is not None:
            env[sentence.bind] = last
            last = None


def _evaluate(expr: ast.Expr, last, env: dict):
    match expr:
        case ast.Number(value=value) | ast.String(value=value):
            return value
        case ast.Sore() | ast.PreviousResult():
            return last
        case ast.Name(name=name):
            return env[name]
        case ast.ListExpr(items=items):
            return [_evaluate(item, last, env) for item in items]
        case ast.Negate(operand=operand):
            return values.subtract(0, _evaluate(operand, last, env))
        case ast.BinaryOp(op=op, left=left, right=right):
            a, b = _evaluate(left, last, env), _evaluate(right, last, env)
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
