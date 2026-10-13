"""意味解析：对号入座（语法设计第 3 节）

構文解析只知道"这句话里出现了哪些助词和值"。意味解析查动词定义，回答：
    1. 每个助词对应哪个参数槽？         → 不认识的助词、重复的助词要报错
    2. 空着的槽能不能用上一步的结果填？ → "唯一空着的槽"规则
    3. 必需的参数都齐了吗？
    4. 类型对不对？                     → 例如不能把文字列拿去相加
    5. 名字定义过吗？类型有没有被改变？   → 符号表（语法设计 5.5）
    6. もし文之后，变量是不是一定有值？   → 确定赋值（语法设计 6.6）

能在运行前发现的错误，都在这里发现。通过检查的程序，执行时只可能遇到
"运行时才知道"的问题（比如除以 0）。
"""

from __future__ import annotations

from dataclasses import dataclass

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.source import Source, Span
from dandori.plugin.registry import Param, Registry, VerbSpec
from dandori.types import Type


# ---------- 意味解析的产物：对好号的程序 ----------

@dataclass(frozen=True)
class BoundStep:
    """一个手順：动词定义 + 按参数名排好的值"""
    spec: VerbSpec
    args: dict[str, ast.Expr]
    span: Span


@dataclass(frozen=True)
class BoundSentence:
    steps: tuple[BoundStep, ...]
    span: Span
    bind: str | None = None      # 句末「〜とする」起的名字


@dataclass(frozen=True)
class BoundAssignment:
    name: str
    value: ast.Expr
    span: Span


@dataclass(frozen=True)
class BoundBranch:
    condition: ast.Condition | None      # None 表示「そうでなければ」
    body: tuple[BoundStatement, ...]


@dataclass(frozen=True)
class BoundIf:
    branches: tuple[BoundBranch, ...]
    span: Span


BoundStatement = BoundSentence | BoundAssignment | BoundIf


@dataclass(frozen=True)
class BoundProgram:
    sentences: tuple[BoundStatement, ...]


def analyze(program: ast.Program, registry: Registry, source: Source
            ) -> tuple[BoundProgram, list[Diagnostic]]:
    return _Analyzer(registry, source).program(program)


# ---------- 实现 ----------

class _Analyzer:
    def __init__(self, registry: Registry, source: Source):
        self.registry = registry
        self.source = source
        self.errors: list[Diagnostic] = []
        # 最近一个手順的结果类型。None 表示"没有结果"：
        # 程序刚开始，或者上一步是「表示する」这种不产生结果的动词
        self.last_type: Type | None = None
        # 符号表：名字 → 类型。类型为 None 表示定义时的值有错误，类型未知
        self.symbols: dict[str, Type | None] = {}
        # 此时"一定有值"的名字。symbols 记录"出现过的名字和类型"，
        # defined 记录"不论走了哪个分支，此时都已经赋过值"的名字
        self.defined: set[str] = set()

    def program(self, program: ast.Program) -> tuple[BoundProgram, list[Diagnostic]]:
        return BoundProgram(self._statements(program.sentences)), self.errors

    def _statements(self, statements) -> tuple[BoundStatement, ...]:
        bound = []
        for statement in statements:
            match statement:
                case ast.Assignment():
                    bound.append(self._assignment(statement))
                case ast.If():
                    bound.append(self._if(statement))
                case _:
                    bound.append(self._sentence(statement))
        return tuple(bound)

    # ---------- もし文 ----------

    def _if(self, node: ast.If) -> BoundIf:
        """确定赋值：每个分支都从进入もし之前的状态开始，结束后取所有分支的交集

            もし…ならば、区分は「大口」とする。               → {区分}
            そうでなければ、区分は「小口」とする。             → {区分}
            ここまで。                                        交集 {区分}：区分 一定有值

        没有「そうでなければ」时，"一个分支都没走"也是一种可能，此时什么都没定义。
        """
        outer_last = self.last_type
        before = set(self.defined)
        outcomes = []
        branches = []

        for branch in node.branches:
            self.defined = set(before)
            if branch.condition is not None:
                self.last_type = outer_last          # 条件属于外层，「それ」指向もし之前的结果
                self._check_condition(branch.condition)
            self.last_type = None                    # 「それ」不跨越块的边界
            body = self._statements(branch.body)
            outcomes.append(self.defined)
            branches.append(BoundBranch(branch.condition, body))

        if node.branches[-1].condition is not None:
            outcomes.append(before)                  # 没有「そうでなければ」：可能一个分支都不走
        self.defined = set.intersection(*outcomes)
        self.last_type = None                        # 事先不知道走了哪个分支
        return BoundIf(tuple(branches), node.span)

    def _check_condition(self, condition: ast.Condition) -> None:
        if isinstance(condition, ast.Logical):
            for item in condition.items:
                self._check_comparison(item)
        else:
            self._check_comparison(condition)

    def _check_comparison(self, comparison: ast.Comparison) -> None:
        left = self._type_of(comparison.left)
        right = self._type_of(comparison.right)

        if comparison.op in (">=", "<=", "<", ">"):
            # 以上・以下・未満・超える：只能比较数
            for expr, actual in ((comparison.left, left), (comparison.right, right)):
                if actual is not None and actual is not Type.NUMBER:
                    self._error("E3012", expr.span, op=ast.COMPARISON_WORDS[comparison.op],
                                actual=self._describe_actual(expr, actual))
        elif left is not None and right is not None:
            # 等于・不等于：类型相同，且为数或文字列
            comparable = (Type.NUMBER, Type.STRING)
            if left is not right or left not in comparable:
                self._error("E3013", comparison.span, left=left.value, right=right.value)

    def _assignment(self, assignment: ast.Assignment) -> BoundAssignment:
        """税率は0.1とする。"""
        value_type = self._type_of(assignment.value)   # 先算右边：「合計は合計+1とする」要求合計已定义
        self._define(assignment.target, value_type)
        self.last_type = None                          # 赋值不产生结果（语法设计 5.5）
        return BoundAssignment(assignment.target.name, assignment.value, assignment.span)

    def _define(self, target: ast.NameRef, new: Type | None) -> None:
        """登记名字。重新赋值可以，改变类型不行"""
        self.defined.add(target.name)
        old = self.symbols.get(target.name)
        if old is not None and new is not None and old is not new:
            self._error("E3009", target.span, name=target.name, old=old.value, new=new.value)
            return
        if target.name not in self.symbols or old is None:
            self.symbols[target.name] = new

    def _sentence(self, sentence: ast.Sentence) -> BoundSentence:
        steps = []
        for i, step in enumerate(sentence.steps):
            # 自动填入只在同一个文内有效（语法设计 3.4）
            can_fill = i > 0 and self.last_type is not None
            steps.append(self._step(step, can_fill))

        bind = sentence.bind
        if bind is not None:
            last = steps[-1].spec
            if last.returns is None:
                self._error("E3011", bind.span, verb=last.dictionary_form)
            else:
                self._define(bind, last.returns)
            self.last_type = None
        return BoundSentence(tuple(steps), sentence.span, bind.name if bind else None)

    def _step(self, step: ast.Step, can_fill: bool) -> BoundStep:
        spec = self.registry.verb(step.verb.verb_id)
        verb = spec.dictionary_form
        bound: dict[str, ast.Expr] = {}

        # 1. 按助词对号入座：只看助词，不看位置
        for arg in step.args:
            param = spec.param_for(arg.particle)
            if param is None:
                self._error("E3001", arg.span, verb=verb, particle=arg.particle)
            elif param.name in bound:
                self._error("E3002", arg.span, particle=arg.particle)
            else:
                bound[param.name] = arg.value

        # 2. 上一步的结果填进"唯一空着的槽"（语法设计 3.3）
        fill_failed = False
        if can_fill and not any(_uses_sore(arg.value) for arg in step.args):
            empty = [p for p in spec.params if p.name not in bound]
            if len(empty) == 1:
                bound[empty[0].name] = ast.PreviousResult(step.verb.span)
            elif not empty:
                self._error("E3005", step.span)
            else:
                self._error("E3004", step.verb.span)
                fill_failed = True

        # 3. 必需的参数都齐了吗（E3004 已经报过的就不重复报）
        if not fill_failed:
            for param in spec.params:
                if param.required and param.name not in bound:
                    self._error("E3003", step.verb.span, verb=verb, particle=param.particle)

        # 4. 类型检查
        for param in spec.params:
            if param.name in bound:
                self._check_arg(spec, param, bound[param.name])

        self.last_type = spec.returns
        return BoundStep(spec, bound, step.span)

    # ---------- 类型 ----------

    def _check_arg(self, spec: VerbSpec, param: Param, expr: ast.Expr) -> None:
        actual = self._type_of(expr)
        if actual is not None and actual not in param.accepts:
            self._error("E3007", expr.span, verb=spec.dictionary_form, particle=param.particle,
                        expected=_describe_types(param.accepts),
                        actual=self._describe_actual(expr, actual))

    def _type_of(self, expr: ast.Expr) -> Type | None:
        """推断类型。返回 None 表示"已经报过错，类型未知"，调用方就不再追加错误"""
        match expr:
            case ast.Number():
                return Type.NUMBER
            case ast.String():
                return Type.STRING
            case ast.Sore():
                if self.last_type is None:
                    self._error("E3006", expr.span)
                return self.last_type
            case ast.PreviousResult():
                return self.last_type
            case ast.Name(name=name):
                if name not in self.symbols:
                    self._error("E3010", expr.span, name=name)
                    return None
                if name not in self.defined:
                    # 在某些分支里定义过，但不是所有情况下都有值
                    self._error("E3014", expr.span, name=name)
                return self.symbols[name]
            case ast.ErrorExpr():
                return None
            case ast.ListExpr(items=items):
                types = [self._type_of(item) for item in items]
                if None in types:
                    return None
                return Type.NUMBER_LIST if all(t is Type.NUMBER for t in types) else Type.LIST
            case ast.BinaryOp(left=left, right=right):
                self._expect_number(left)
                self._expect_number(right)
                return Type.NUMBER
            case ast.Negate(operand=operand):
                self._expect_number(operand)
                return Type.NUMBER
        raise TypeError(f"未知のノード：{expr!r}")

    def _expect_number(self, expr: ast.Expr) -> None:
        actual = self._type_of(expr)
        if actual is not None and actual is not Type.NUMBER:
            self._error("E3008", expr.span, actual=self._describe_actual(expr, actual))

    def _describe_actual(self, expr: ast.Expr, actual: Type) -> str:
        if isinstance(expr, ast.PreviousResult):
            return f"前の結果は{actual.value}です"
        text = self.source.slice(expr.span)
        if isinstance(expr, ast.String):
            quoted = text                     # 本身就带着「」
        elif "「" in text:
            quoted = f"『{text}』"            # 引用里还有「」时，外层用『』
        else:
            quoted = f"「{text}」"
        return f"{quoted}は{actual.value}です"

    def _error(self, code: str, span: Span, **params: str) -> None:
        self.errors.append(Diagnostic.make(code, span, **params))


def _describe_types(types: frozenset[Type]) -> str:
    if types <= {Type.NUMBER, Type.NUMBER_LIST}:
        return "数"
    return "か".join(t.value for t in types)


def _uses_sore(expr: ast.Expr) -> bool:
    match expr:
        case ast.Sore():
            return True
        case ast.ListExpr(items=items):
            return any(_uses_sore(item) for item in items)
        case ast.BinaryOp(left=left, right=right):
            return _uses_sore(left) or _uses_sore(right)
        case ast.Negate(operand=operand):
            return _uses_sore(operand)
    return False
