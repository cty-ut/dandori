"""意味解析：对号入座（语法设计第 3 节）

構文解析只知道"这句话里出现了哪些助词和值"。意味解析查动词定义，回答：
    1. 每个助词对应哪个参数槽？         → 不认识的助词、重复的助词要报错
    2. 空着的槽能不能用上一步的结果填？ → "唯一空着的槽"规则
    3. 必需的参数都齐了吗？
    4. 类型对不对？                     → 例如不能把文字列拿去相加
    5. 名字定义过吗？类型有没有被改变？   → 符号表（语法设计 5.5）
    6. もし文之后，变量是不是一定有值？   → 确定赋值（语法设计 6.6）
    7. 循环变量有没有被改动、被用到循环外？（语法设计 7.4）
    8. 表有没有这一列？（语法设计 8.4）——「読み込む」在这里就读取文件，确定列定义
    9. 逐行处理中的列名指哪张表？有歧义吗？（语法设计 10.3、10.4）
   10. 程序会读取、写出哪些文件？（语法设计 11.6、11.8）——「説明」命令据此回答
   11. 设定文写得对吗？（语法设计 16.2）

能在运行前发现的错误，都在这里发现。通过检查的程序，执行时只可能遇到
"运行时才知道"的问题（比如除以 0）。
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.source import Source, Span
from dandori.plugin.registry import (Effect, InferContext, Param, ParamKind, Registry, SettingKind, VerbError,
                                     VerbSpec)
from dandori import dates
from dandori.money import Money
from dandori.types import ELEMENT_OF, LIST_OF, NUMERIC, NUMERIC_LISTS, TableType, Type, ValueType

_ORDERED = NUMERIC | {Type.DATE}          # 可以比较大小的类型
_DATE_UNITS = {Type.BUSINESS_DAYS, Type.MONTHS}   # 只能与日付相加减的带单位的数

# "有结果，但类型未知"（如文件读取失败）。与 None（没有结果）区分开，避免后续连锁报错
_UNKNOWN = object()


def _known(t):
    return None if t is _UNKNOWN else t


# ---------- 意味解析的产物：对好号的程序 ----------

@dataclass(frozen=True)
class BoundStep:
    """一个手順：动词定义 + 按参数名排好的值"""
    spec: VerbSpec
    args: dict[str, ast.Expr]
    span: Span
    result_type: ValueType | None = None    # 结果的类型（「読み込む」由编译时读取的文件决定）


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


@dataclass(frozen=True)
class BoundCountLoop:
    count: ast.Expr
    body: tuple[BoundStatement, ...]
    span: Span


@dataclass(frozen=True)
class BoundRangeLoop:
    var: str
    start: ast.Expr
    end: ast.Expr
    body: tuple[BoundStatement, ...]
    span: Span


@dataclass(frozen=True)
class BoundEachLoop:
    table: ast.Expr
    body: tuple[BoundStatement, ...]
    span: Span


@dataclass(frozen=True)
class BoundSetting:
    """设定文：值在编译时就确定了（列名、上限、文件名），执行时登记到 ctx.settings"""
    namespace: str
    key: str
    value: object


BoundStatement = (BoundSentence | BoundAssignment | BoundIf | BoundCountLoop | BoundRangeLoop
                  | BoundEachLoop | BoundSetting | ast.Break | ast.Continue)


@dataclass(frozen=True)
class Resource:
    """程序会碰的一个文件（语法设计 11.6）"""
    effect: Effect
    path: str
    explain: str           # 如「「集計.csv」を新規作成します」
    conditional: bool      # 写在もし里：不一定执行
    span: Span


@dataclass(frozen=True)
class BoundProgram:
    sentences: tuple[BoundStatement, ...]
    resources: tuple[Resource, ...] = ()
    rule_data: tuple[str, ...] = ()       # 使用的规则数据的说明（如祝日表的来源和范围）


def analyze(program: ast.Program, registry: Registry, source: Source, base_dir: Path | None = None
            ) -> tuple[BoundProgram, list[Diagnostic]]:
    return _Analyzer(registry, source, base_dir or Path.cwd()).program(program)


# ---------- 实现 ----------

class _Analyzer:
    def __init__(self, registry: Registry, source: Source, base_dir: Path):
        self.registry = registry
        self.source = source
        self.settings: dict[tuple[str, str], object] = {}
        self.infer_context = InferContext(base_dir, self.settings)
        self.errors: list[Diagnostic] = []
        # 最近一个手順的结果类型。None 表示"没有结果"：
        # 程序刚开始，或者上一步是「表示する」这种不产生结果的动词
        self.last_type: ValueType | None = None
        # 符号表：名字 → 类型。类型为 None 表示定义时的值有错误，类型未知
        self.symbols: dict[str, ValueType | None] = {}
        # 此时"一定有值"的名字。symbols 记录"出现过的名字和类型"，
        # defined 记录"不论走了哪个分支，此时都已经赋过值"的名字
        self.defined: set[str] = set()
        self.loop_vars: set[str] = set()         # 正在执行的循环的变量：不能被赋值
        self.ended_loop_vars: set[str] = set()   # 用过的循环变量：在循环外使用时给出专门的提示
        # 列的作用域栈：逐行处理的循环、「〜のもの」的条件都会压入一张表。
        # 在这些地方，名字被理解为表的列（语法设计 9.4、10.3）。元素是 (表的类型, 给人看的表名)
        self.scopes: list[tuple[TableType, str]] = []
        self.loop_depth = 0                      # 「繰り返しをやめる」「次に進む」只能在循环中使用
        # 对类型未知的表（之前已经报过错）逐行处理时：循环中不认识的名字不再报错，避免连锁错误
        self.unknown_scope_depth = 0
        self.if_depth = 0                        # もし文中的写出：「説明」中标注"条件によって"
        self.resources: list[Resource] = []
        self.rule_data: list[str] = []           # 使用的规则数据的名字（如「祝日」）

    def program(self, program: ast.Program) -> tuple[BoundProgram, list[Diagnostic]]:
        statements = self._statements(program.sentences)
        self._check_resources()
        rule_data = tuple(f"{name}：{self.registry.rule_data[name]}" for name in self.rule_data)
        return BoundProgram(statements, tuple(self.resources), rule_data), self.errors

    def _use(self, names) -> None:
        """记录程序使用了某种规则数据（「説明」中显示其版本，语法设计 14.2）"""
        for name in (names,) if isinstance(names, str) else (names or ()):
            if name not in self.rule_data:
                self.rule_data.append(name)

    def _check_resources(self) -> None:
        """同一个文件写两次（E3040）。写出后再读取（E3041）在 _step 中、尝试读取之前检查"""
        written: set[str] = set()
        for resource in self.resources:
            if resource.effect is Effect.WRITE:
                if resource.path in written:
                    self._error("E3040", resource.span, file=resource.path)
                written.add(resource.path)

    def _statements(self, statements) -> tuple[BoundStatement, ...]:
        bound = []
        for statement in statements:
            match statement:
                case ast.Assignment():
                    bound.append(self._assignment(statement))
                case ast.If():
                    bound.append(self._if(statement))
                case ast.CountLoop():
                    bound.append(self._count_loop(statement))
                case ast.RangeLoop():
                    bound.append(self._range_loop(statement))
                case ast.EachLoop():
                    bound.append(self._each_loop(statement))
                case ast.Setting():
                    bound.append(self._setting(statement))
                case ast.Break() | ast.Continue():
                    if self.loop_depth == 0:
                        word = "繰り返しをやめる" if isinstance(statement, ast.Break) else "次に進む"
                        self._error("E3035", statement.span, word=word)
                    self.last_type = None
                    bound.append(statement)
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
            self.if_depth += 1
            body = self._statements(branch.body)
            self.if_depth -= 1
            outcomes.append(self.defined)
            branches.append(BoundBranch(branch.condition, body))

        if node.branches[-1].condition is not None:
            outcomes.append(before)                  # 没有「そうでなければ」：可能一个分支都不走
        self.defined = set.intersection(*outcomes)
        self.last_type = None                        # 事先不知道走了哪个分支
        return BoundIf(tuple(branches), node.span)

    # ---------- 設定文（语法设计 16.2） ----------

    def _setting(self, node: ast.Setting) -> BoundSetting:
        spec = self.registry.setting(node.namespace, node.key)
        if spec is None:
            keys = "、".join(self.registry.setting_keys(node.namespace)) or "設定はありません"
            self._error("E2022", node.key_span, namespace=node.namespace, key=node.key, keys=keys)
            return BoundSetting(node.namespace, node.key, None)
        if self.if_depth or self.loop_depth:
            self._error("E3054", node.span)

        errors_before = len(self.errors)
        value = self._setting_value(spec.kind, node)
        if value is None and len(self.errors) == errors_before:
            self._error("E3055", node.value.span, namespace=node.namespace, key=node.key,
                        example=spec.kind.value)
        elif value is not None and spec.check is not None:
            try:
                spec.check(value, self.infer_context)
            except VerbError as e:
                self._error(e.code, node.value.span, **e.params)
        if value is not None and spec.kind is SettingKind.FILE:
            self.resources.append(Resource(Effect.READ, value, f"「{value}」を読み込みます", False, node.value.span))
        self.settings[(node.namespace, node.key)] = value
        return BoundSetting(node.namespace, node.key, value)

    def _setting_value(self, kind: SettingKind, node: ast.Setting):
        """按设定的种类检查值的写法。写法不对时返回 None"""
        value = node.value
        if kind is SettingKind.COLUMN_PAIR:
            if node.up_to or not (isinstance(value, ast.ListExpr) and len(value.items) == 2
                                  and all(isinstance(i, ast.ColumnRef) for i in value.items)):
                return None
            types = [self._type_of(item) for item in value.items]
            if None in types:
                return None
            return tuple((item.column, ELEMENT_OF.get(t, t)) for item, t in zip(value.items, types))
        if kind is SettingKind.COLUMN:
            if node.up_to or not isinstance(value, ast.ColumnRef):
                return None
            t = self._type_of(value)
            return None if t is None else (value.column, ELEMENT_OF.get(t, t))
        if kind is SettingKind.LIMIT:
            if not node.up_to or not isinstance(value, ast.Number):
                return None
            return value.value
        if kind is SettingKind.FILE:
            if node.up_to or not isinstance(value, ast.String):
                return None
            return value.value
        return None

    # ---------- 繰り返し文 ----------

    def _count_loop(self, node: ast.CountLoop) -> BoundCountLoop:
        self._expect_loop_number(node.count, "回数")
        body = self._loop_body(node.body)
        return BoundCountLoop(node.count, body, node.span)

    def _range_loop(self, node: ast.RangeLoop) -> BoundRangeLoop:
        self._expect_loop_number(node.start, "始め")
        self._expect_loop_number(node.end, "終わり")

        name = node.var.name
        conflict = (name in self.defined or name in self.loop_vars
                    or self.symbols.get(name, Type.NUMBER) is not Type.NUMBER)
        if conflict:
            self._error("E3016", node.var.span, name=name)
            body = self._loop_body(node.body)
        else:
            self.symbols[name] = Type.NUMBER
            self.loop_vars.add(name)
            body = self._loop_body(node.body, loop_var=name)
            self.loop_vars.discard(name)
            self.ended_loop_vars.add(name)
        return BoundRangeLoop(name, node.start, node.end, body, node.span)

    def _each_loop(self, node: ast.EachLoop) -> BoundEachLoop:
        """明細のそれぞれについて：循环中直接写列名，表示当前这一行（语法设计 10.3）"""
        table = self._type_of(node.table)
        if table is not None and not isinstance(table, TableType):
            self._error("E3036", node.table.span, actual=self._describe_actual(node.table, table))
            table = None
        if table is not None:
            self.scopes.append((table, self._table_label(node.table)))
        else:
            self.unknown_scope_depth += 1
        body = self._loop_body(node.body)
        if table is not None:
            self.scopes.pop()
        else:
            self.unknown_scope_depth -= 1
        return BoundEachLoop(node.table, body, node.span)

    def _loop_body(self, statements, loop_var: str | None = None) -> tuple[BoundStatement, ...]:
        """循环可能执行 0 次：循环里新定义的变量，循环之后都不算"一定有值"

        循环变量本身只在循环内有值。
        """
        before = set(self.defined)
        self.defined = before | ({loop_var} if loop_var else set())
        self.last_type = None                        # 「それ」不跨越块的边界
        self.loop_depth += 1
        body = self._statements(statements)
        self.loop_depth -= 1
        self.defined = before
        self.last_type = None
        return body

    def _expect_loop_number(self, expr: ast.Expr, part: str) -> None:
        actual = self._type_of(expr)                 # 在循环外计算：「それ」指向循环之前的结果
        if actual is not None and actual is not Type.NUMBER:
            self._error("E3015", expr.span, part=part, actual=self._describe_actual(expr, actual))

    # ---------- 条件 ----------

    def _check_condition(self, condition: ast.Condition) -> None:
        if isinstance(condition, ast.Logical):
            for item in condition.items:
                self._check_comparison(item)
        else:
            self._check_comparison(condition)

    def _check_comparison(self, comparison: ast.Comparison) -> None:
        if isinstance(comparison.right, ast.DayKind):
            # 期日が休日：只能对日付使用（语法设计 14.4）
            actual = self._type_of(comparison.left)
            if actual is not None and actual is not Type.DATE:
                self._error("E3051", comparison.left.span,
                            actual=self._describe_actual(comparison.left, actual))
            self._use("祝日")
            return

        # 与空欄比较：只能用「が空欄」「が空欄でない」（语法设计 9.7）
        blank_sides = [isinstance(side, ast.Blank) for side in (comparison.left, comparison.right)]
        if any(blank_sides):
            if comparison.op not in ("=", "<>"):
                self._error("E3032", comparison.span)
            for side, is_blank in zip((comparison.left, comparison.right), blank_sides):
                if not is_blank:
                    self._type_of(side)
            return

        left = self._type_of(comparison.left)
        right = self._type_of(comparison.right)

        if comparison.op in (">=", "<=", "<", ">"):
            # 比较大小：数、金額（语法设计 12.4）、日付（语法设计 13.4）
            for expr, actual in ((comparison.left, left), (comparison.right, right)):
                if actual is not None and actual not in _ORDERED:
                    self._error("E3012", expr.span, op=ast.COMPARISON_WORDS[comparison.op],
                                actual=self._describe_actual(expr, actual))
                    return
            if left is not None and right is not None and (left is Type.DATE) != (right is Type.DATE):
                self._error("E3013", comparison.span, left=left.value, right=right.value)
                return
            self._check_comparison_word(comparison, left)
        elif left is not None and right is not None:
            # 等于・不等于：类型相同的数、文字列、日付；数与金額之间也可以
            same = left is right and left in (Type.NUMBER, Type.STRING, Type.MONEY, Type.DATE)
            if not same and not (left in NUMERIC and right in NUMERIC):
                self._error("E3013", comparison.span, left=left.value, right=right.value)

    def _check_comparison_word(self, comparison: ast.Comparison, left) -> None:
        """日付用「以前・以降・より前・より後」，数用「以上・以下・未満・超える」（语法设计 13.4）"""
        word = comparison.word
        if word is None or left is None:
            return                                   # 符号对两者都可以
        is_date_word = word in ast.DATE_COMPARISON_WORDS.values()
        if left is Type.DATE and not is_date_word:
            self._error("E3047", comparison.span)
        elif left is not Type.DATE and is_date_word:
            self._error("E3048", comparison.span, word=word)

    def _assignment(self, assignment: ast.Assignment) -> BoundAssignment:
        """税率は0.1とする。"""
        value_type = self._type_of(assignment.value)   # 先算右边：「合計は合計+1とする」要求合計已定义
        self._define(assignment.target, value_type)
        self.last_type = None                          # 赋值不产生结果（语法设计 5.5）
        return BoundAssignment(assignment.target.name, assignment.value, assignment.span)

    def _define(self, target: ast.NameRef, new: ValueType | None) -> None:
        """登记名字。重新赋值可以，改变类型不行，循环变量不能改，不能和当前行的列同名"""
        if target.name in self.loop_vars:
            self._error("E3017", target.span, name=target.name)
            return
        if self._scopes_with(target.name):
            self._error("E3029", target.span, name=target.name)
            return
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
            last = steps[-1]
            if last.result_type is None:
                self._error("E3011", bind.span, verb=last.spec.dictionary_form)
            else:
                self._define(bind, _known(last.result_type))
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

        # 2. 上一步的结果填入空着的槽（语法设计 3.3，9.6 细化）
        fill_failed = False
        if can_fill and not any(_uses_sore(arg.value) for arg in step.args):
            # 只有"值"类的槽参与；必需槽优先，必需槽都满了再看可选槽
            empty = [p for p in spec.params if p.kind is ParamKind.VALUE and p.name not in bound]
            required = [p for p in empty if p.required]
            target = required if required else empty
            if len(target) == 1:
                bound[target[0].name] = ast.PreviousResult(step.verb.span)
            elif not target:
                self._error("E3005", step.span)
            else:
                self._error("E3004", step.verb.span)
                fill_failed = True

        # 3. 必需的参数都齐了吗（E3004 已经报过的就不重复报）
        missing = [p for p in spec.params if p.required and p.name not in bound]
        if not fill_failed:
            for param in missing:
                self._error("E3003", step.verb.span, verb=verb, particle=param.particle)

        # 4. 类型检查。先查"值""列"等自己就有类型的槽，
        #    再查条件、列名、排序的槽：它们的列来自前者的表（columns_of）
        errors_before = len(self.errors)
        types: dict[str, ValueType | None] = {}
        for param in spec.params:
            if param.name in bound and param.kind in (ParamKind.VALUE, ParamKind.COLUMN, ParamKind.BLANKS):
                types[param.name] = self._check_arg(spec, param, bound[param.name])
        for param in spec.params:
            if param.name in bound and param.kind in (ParamKind.CONDITION, ParamKind.COLUMNS, ParamKind.SORT):
                source = bound.get(param.columns_of)
                # 列来自的那个槽不是表时（已经报过 E3007），不再检查列名
                scope = (_table_or_none(types.get(param.columns_of)), self._table_label(source))
                self._check_column_arg(spec, param, bound[param.name], scope)

        # 写出后再读取同一个文件：编译时读到的是写出前的旧内容（或者文件还不存在），
        # 在尝试读取之前就报错，给出真正的原因（E3041），而不是"找不到文件"
        path = bound.get(spec.resource_param) if spec.resource_param else None
        if (spec.effect is Effect.READ and isinstance(path, ast.String)
                and any(r.effect is Effect.WRITE and r.path == path.value for r in self.resources)):
            self._error("E3041", path.span, file=path.value)

        # 5. 结果的类型。取决于参数的（「読み込む」「合計する」等），由动词定义的 infer 在编译时决定
        result = spec.returns
        if spec.infer is not None:
            result = _UNKNOWN
            if not missing and not fill_failed and len(self.errors) == errors_before:
                try:
                    inferred = spec.infer(bound, types, self.infer_context)
                    result = _UNKNOWN if inferred is None else inferred
                except VerbError as e:
                    span = next(iter(bound.values())).span if bound else step.span
                    self._error(e.code, span, **e.params)

        # 6. 编译时的额外检查（「書き出す」：文件是否已存在等）
        if spec.check is not None and not missing and not fill_failed and len(self.errors) == errors_before:
            try:
                spec.check(bound, types, self.infer_context)
            except VerbError as e:
                span = bound[spec.resource_param].span if spec.resource_param in bound else step.span
                self._error(e.code, span, **e.params)

        self._use(spec.uses)

        # 7. 记录程序会碰的文件
        if spec.effect is Effect.WRITE and self.loop_depth > 0:
            self._error("E3042", step.span)
        if isinstance(path, ast.String) and len(self.errors) == errors_before:
            self.resources.append(Resource(spec.effect, path.value, spec.explain.format(path=path.value),
                                           self.if_depth > 0, path.span))

        self.last_type = result
        return BoundStep(spec, bound, step.span, result)

    # ---------- 类型 ----------

    def _check_arg(self, spec: VerbSpec, param: Param, expr: ast.Expr) -> ValueType | None:
        """"值""列""空欄指定"的槽。返回类型（列、空欄指定返回所在的表的类型），供 columns_of 和 infer 使用"""
        if param.kind is ParamKind.COLUMN:
            if not isinstance(expr, ast.ColumnRef):
                return self._wrong_kind(spec, param, expr)
            self._check_accepts(spec, param, expr, self._type_of(expr))
            return _table_or_none(self._type_of(expr.table))

        if param.kind is ParamKind.BLANKS:
            if not (isinstance(expr, ast.BlanksOf) and isinstance(expr.target, ast.ColumnRef)):
                return self._wrong_kind(spec, param, expr)
            self._type_of(expr.target)                       # 检查列名
            return _table_or_none(self._type_of(expr.target.table))

        if isinstance(expr, (ast.RowFilter, ast.SortKey, ast.BlanksOf)):
            return self._wrong_kind(spec, param, expr)
        actual = self._type_of(expr)
        self._check_accepts(spec, param, expr, actual)
        return actual

    def _check_accepts(self, spec: VerbSpec, param: Param, expr: ast.Expr, actual) -> None:
        if actual is None or param.accepts is None:
            return
        if actual in param.accepts or (isinstance(actual, TableType) and TableType in param.accepts):
            return
        self._error("E3007", expr.span, verb=spec.dictionary_form, particle=param.particle,
                    expected=_describe_types(param.accepts),
                    actual=self._describe_actual(expr, actual))

    def _check_column_arg(self, spec: VerbSpec, param: Param, expr: ast.Expr, scope) -> None:
        """条件、列名、排序的槽：名字被理解为 scope 这张表的列（语法设计 9.4）"""
        table, label = scope
        if param.kind is ParamKind.CONDITION:
            if not isinstance(expr, ast.RowFilter):
                self._wrong_kind(spec, param, expr)
            elif table is not None:
                self.scopes.append((table, label))
                self._check_condition(expr.condition)
                self.scopes.pop()
            return

        if param.kind is ParamKind.SORT:
            if not isinstance(expr, ast.SortKey):
                self._wrong_kind(spec, param, expr)
                return
            names = [expr.target]
        else:
            names = list(expr.items) if isinstance(expr, ast.ListExpr) else [expr]

        for name in names:
            if ast.column_name(name) is None:
                self._error("E3030", name.span, verb=spec.dictionary_form, particle=param.particle)
            elif table is not None:
                self._column_name(name, table, label)

    def _column_name(self, expr: ast.Name | ast.String, table: TableType, label: str) -> ValueType | None:
        """作为列名的名字或「」：必须是这张表的列。名字不能和变量重名（「」本来就不是变量）"""
        name = ast.column_name(expr)
        column = table.column(name)
        if column is None:
            self._no_such_column(name, expr.span, table, label)
            return None
        if isinstance(expr, ast.Name) and name in self.symbols:
            self._error("E3029", expr.span, name=name)
        return column

    def _scopes_with(self, name: str) -> list[tuple[TableType, str]]:
        return [scope for scope in self.scopes if name in scope[0].names]

    def _scoped_column(self, expr: ast.Name) -> ValueType | None:
        matches = self._scopes_with(expr.name)
        if len(matches) > 1:
            tables = "と".join(f"「{label}」" for _, label in matches)
            self._error("E3037", expr.span, name=expr.name, tables=tables)
            return None
        if matches:
            table, label = matches[0]
            return self._column_name(expr, table, label)
        # 既不是列也不是变量：多半是列名写错了，按最内层的表给出建议
        table, label = self.scopes[-1]
        self._no_such_column(expr.name, expr.span, table, label)
        return None

    def _no_such_column(self, column: str, span: Span, table: TableType, label: str) -> None:
        close = difflib.get_close_matches(column, table.names, n=1, cutoff=0.5)
        hint = f"「{close[0]}」のことですか？" if close else f"列：{'、'.join(table.names)}"
        self._error("E3025", span, table=label, column=column, hint=hint)

    def _wrong_kind(self, spec: VerbSpec, param: Param, expr: ast.Expr) -> None:
        self._error("E3031", expr.span, verb=spec.dictionary_form, particle=param.particle,
                    expected=_KIND_EXAMPLES[param.kind])
        return None

    def _table_label(self, expr: ast.Expr | None) -> str:
        if expr is None or isinstance(expr, (ast.PreviousResult, ast.Sore)):
            return "前の結果"
        if isinstance(expr, ast.ColumnRef):
            return self._table_label(expr.table)
        return self.source.slice(expr.span)

    def _type_of(self, expr: ast.Expr) -> ValueType | None:
        """推断类型。返回 None 表示"已经报过错，类型未知"，调用方就不再追加错误"""
        match expr:
            case ast.Number(value=dates.BusinessDays()):      # 必须在一般的「数」之前匹配
                self._use("祝日")
                return Type.BUSINESS_DAYS
            case ast.Number(value=dates.Months()):
                return Type.MONTHS
            case ast.Number(value=value):
                return Type.MONEY if isinstance(value, Money) else Type.NUMBER
            case ast.DateLiteral(value=value):
                return Type.DATE if value is not None else None       # None：不存在的日期，已报错
            case ast.Today():
                return Type.DATE
            case ast.String():
                return Type.STRING
            case ast.Sore():
                if self.last_type is None:
                    self._error("E3006", expr.span)
                return _known(self.last_type)
            case ast.PreviousResult():
                return _known(self.last_type)
            case ast.ColumnRef():
                return self._column_type(expr)
            case ast.Name(name=name) if self.scopes and (
                    self._scopes_with(name) or name not in self.symbols):
                # 逐行处理、「〜のもの」中的「金額」：当前这一行的金额（标量）
                return self._scoped_column(expr)
            case ast.Name(name=name):
                if name not in self.symbols:
                    if self.unknown_scope_depth == 0:
                        self._error("E3010", expr.span, name=name)
                    return None
                if name not in self.defined:
                    if name in self.ended_loop_vars:
                        self._error("E3018", expr.span, name=name)
                    else:
                        # 在某些分支里定义过，但不是所有情况下都有值
                        self._error("E3014", expr.span, name=name)
                return self.symbols[name]
            case ast.ErrorExpr():
                return None
            case ast.Blank():
                self._error("E3033", expr.span)
                return None
            case ast.RowFilter() | ast.SortKey() | ast.BlanksOf():
                # 条件名词、排序依据等只能写在表格操作的对应参数里
                self._error("E2008", expr.span, word=self.source.slice(expr.span))
                return None
            case ast.ListExpr(items=items):
                types = [self._type_of(item) for item in items]
                if None in types:
                    return None
                if all(t is Type.NUMBER for t in types):
                    return Type.NUMBER_LIST
                if all(t in NUMERIC for t in types):
                    return Type.MONEY_LIST                  # 100円と5：宽松的规则
                if all(t is Type.DATE for t in types):
                    return Type.DATE_LIST
                if all(t is Type.STRING for t in types):
                    return Type.STRING_LIST
                return Type.LIST
            case ast.BinaryOp(op=op, left=left, right=right):
                return self._arithmetic_type(expr, op, self._expect_number(left), self._expect_number(right))
            case ast.Negate(operand=operand):
                operand_type = self._expect_number(operand)
                if operand_type is Type.DATE or operand_type in _DATE_UNITS:
                    self._error("E3045", expr.span)
                return operand_type or Type.NUMBER
        raise TypeError(f"未知のノード：{expr!r}")

    def _column_type(self, expr: ast.ColumnRef) -> Type | None:
        """明細の金額：明細必须是表，而且有「金額」这一列。
        期日の月末：对日付（或日付的並び）使用时，取日付的属性（语法设计 13.5）"""
        table = self._type_of(expr.table)
        if table is None:
            return None
        if table in (Type.DATE, Type.DATE_LIST):
            prop = self.registry.date_property(expr.column)
            if prop is None:
                self._error("E3049", expr.column_span, name=expr.column,
                            names="、".join(self.registry.date_property_names))
                return None
            self._use(prop.uses)
            return prop.result if table is Type.DATE else LIST_OF[prop.result]
        if not isinstance(table, TableType):
            self._error("E3026", expr.span, actual=self._describe_actual(expr.table, table))
            return None
        column = table.column(expr.column)
        if column is None:
            close = difflib.get_close_matches(expr.column, table.names, n=1, cutoff=0.5)
            hint = f"「{close[0]}」のことですか？" if close else f"列：{'、'.join(table.names)}"
            self._error("E3025", expr.column_span, table=self.source.slice(expr.table.span),
                        column=self.source.slice(expr.column_span).strip("「」"), hint=hint)
            return None
        return LIST_OF[column]

    def _expect_number(self, expr: ast.Expr) -> Type | None:
        """算式的一边：必须是数、金額或日付。返回它的类型（不是时报错并返回 None）"""
        actual = self._type_of(expr)
        if actual is not None and actual not in _ORDERED | _DATE_UNITS:
            self._error("E3008", expr.span, actual=self._describe_actual(expr, actual))
            return None
        return actual

    def _arithmetic_type(self, expr: ast.BinaryOp, op: str, left, right) -> Type:
        """金額与数宽松互通（语法设计 12.4）：有一方是金額，结果就是金額。
        金額÷金額 是比例（数）；金額×金額、数÷金額 没有意义"""
        if Type.DATE in (left, right) or {left, right} & _DATE_UNITS:
            return self._date_arithmetic_type(expr, op, left, right)
        left_money, right_money = left is Type.MONEY, right is Type.MONEY
        if op == "*" and left_money and right_money:
            self._error("E3043", expr.span)
        if op == "/":
            if right_money and not left_money and left is not None:
                self._error("E3044", expr.span)
            if left_money and right_money:
                return Type.NUMBER
        return Type.MONEY if left_money or right_money else Type.NUMBER

    def _date_arithmetic_type(self, expr: ast.BinaryOp, op: str, left, right) -> Type | None:
        """日付 ± 数・営業日数・月数 → 日付；日付 − 日付 → 数（天数）；其他都不行（语法设计 13.3、14.4、14.5）"""
        offsets = {Type.NUMBER} | _DATE_UNITS
        if op == "+" and Type.DATE in (left, right) and ({left, right} - {Type.DATE}) <= offsets \
                and left is not right:
            return Type.DATE
        if op == "-" and left is Type.DATE and right in offsets:
            return Type.DATE
        if op == "-" and left is Type.DATE and right is Type.DATE:
            return Type.NUMBER
        if left is not None and right is not None:
            self._error("E3045", expr.span)
        return None

    def _describe_actual(self, expr: ast.Expr, actual: ValueType) -> str:
        if isinstance(expr, ast.PreviousResult):
            return f"前の結果は{actual.value}です"
        text = self.source.slice(expr.span)
        if isinstance(expr, ast.String) or (text.startswith("「") and text.endswith("」")
                                            and text.count("「") == 1):
            quoted = text                     # 本身就带着「」（字符串，或用「」写的列名）
        elif "「" in text:
            quoted = f"『{text}』"            # 引用里还有「」时，外层用『』
        else:
            quoted = f"「{text}」"
        return f"{quoted}は{actual.value}です"

    def _error(self, code: str, span: Span, **params: str) -> None:
        self.errors.append(Diagnostic.make(code, span, **params))


_KIND_EXAMPLES = {
    ParamKind.CONDITION: "「金額が10000以上のもの」のような条件",
    ParamKind.SORT: "「金額の大きい順」のような並べ方",
    ParamKind.COLUMN: "「明細の金額」のような列",
    ParamKind.BLANKS: "「明細の金額の空欄」",
    ParamKind.VALUE: "値",
}


def _table_or_none(t):
    return t if isinstance(t, TableType) else None


def _describe_types(types: frozenset) -> str:
    if types == {TableType}:
        return "表"
    if types <= NUMERIC | NUMERIC_LISTS | _DATE_UNITS:
        return "数"                                  # 営業日数・月数也是"带单位的数"
    if types <= NUMERIC | NUMERIC_LISTS | _DATE_UNITS | {Type.DATE}:
        return "数か日付"
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
        case ast.ColumnRef(table=table):
            return _uses_sore(table)
    return False
