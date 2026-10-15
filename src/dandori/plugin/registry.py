"""注册表（架构设计 5.1、6）

动词定义是"唯一的事实来源"：词典、参数检查、执行都从这里派生。
标准库也通过这个注册表登记，和第三方插件走同一条路，没有特权后门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from dandori.frontend.conjugation import Conjugation
from dandori.types import Type, ValueType


class Effect(Enum):
    """动词的效果（语法设计 11.5）：试运行时只执行"计算、读取、显示"，写入只记录"""

    PURE = "計算"
    READ = "読み込み"
    OUTPUT = "表示"
    WRITE = "書き出し"


class ParamKind(Enum):
    """参数槽的种类（语法设计 9.9）：决定写什么、传给实现函数什么"""

    VALUE = "値"            # 100を、明細を                → 值
    CONDITION = "条件"      # 金額が10000以上のものを       → 对每一行求值的函数 row -> bool
    COLUMNS = "列名"        # 取引先と金額を、取引先ごとに   → 列名的列表
    SORT = "並べ方"         # 金額の大きい順に              → (列名, 是否降序)
    COLUMN = "列"           # 明細の金額を                  → (表, 列名)
    BLANKS = "空欄指定"     # 明細の金額の空欄を            → (表, 列名)


@dataclass(frozen=True)
class Param:
    """参数槽：用助词标记（语法设计 3.2）"""

    particle: str                       # 助词，如「を」
    name: str                           # 实现函数的参数名
    accepts: frozenset | None           # 接受的类型（可包含 TableType 表示"任何表"）；None 表示什么都接受
    required: bool = True
    kind: ParamKind = ParamKind.VALUE
    # 条件、列名、排序的槽：列来自哪个槽的表（如「絞り込む」的条件来自「から」的表）
    columns_of: str | None = None


class VerbError(Exception):
    """动词执行时的错误。由解释器补上原文位置后报告给用户"""

    def __init__(self, code: str, **params: str):
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True)
class InferContext:
    """推断类型时可以用到的信息（编译时）"""
    base_dir: Path                   # 程序文件所在的目录：相对路径以它为准
    # 到目前为止的设定文：(名前空间, 设定名) → 值（语法设计 16.2）
    settings: dict = field(default_factory=dict)
    # 读取文件时的补充说明：(文件名, 表名) → 「見出しは3行目」等，显示在「説明」中（语法设计 19）
    notes: dict = field(default_factory=dict)


class SettingKind(Enum):
    """设定的值的写法（语法设计 16.2）"""

    COLUMN_PAIR = "「入金の振込依頼人と請求のフリガナ」"   # 两个列 → ((列名, 类型), (列名, 类型))
    COLUMN = "「請求の支払期日」"                           # 一个列 → (列名, 类型)
    LIMIT = "「880円まで」"                                 # 上限 → 数或金額
    FILE = "「「〜.csv」」"                                 # 文件名 → 文字列
    TEXT = "「「〜」」"                                     # 文字列（文件以外，如予定）→ 文字列


@dataclass(frozen=True)
class SettingSpec:
    """设定文：消込の手数料は、880円までとする。由插件登记，语言核心不知道它的含义"""
    namespace: str                   # 「消込」
    key: str                         # 「手数料」
    kind: SettingKind
    # 编译时检查值（FILE：读取文件、检查格式；TEXT：检查写法），出错时抛出 VerbError。
    # 可以返回用到的规则数据的名字（如「祝日」），显示在「説明」中
    check: Callable | None = None
    example: str | None = None       # 目录中显示的例子；None 时用 kind 的例子


@dataclass(frozen=True)
class VerbSpec:
    id: str                          # 完整标识，如 "std.足す"
    dictionary_form: str             # 辞书形，如 "足す"
    conjugation: Conjugation         # 活用类型
    params: tuple[Param, ...]        # 参数槽
    returns: ValueType | None        # 结果的类型；None 表示没有结果（如 表示する）
    impl: Callable                   # 实现：impl(ctx, **按参数名传入的值)
    # 结果的类型取决于参数时（如「読み込む」取决于文件的列，「合計する」取决于有没有「ごとに」），
    # 在编译时调用：infer(参数名 → 语法树, 参数名 → 类型, InferContext) → 类型。
    # 返回 None 表示类型未知（参数有错误时），出错时抛出 VerbError
    infer: Callable | None = None
    # 编译时的额外检查（如「書き出す」：文件是否已存在），签名与 infer 相同，出错时抛出 VerbError
    check: Callable | None = None
    effect: Effect = Effect.PURE
    # 读取、写出的动词：哪个参数是文件名（「説明」据此列出程序会碰哪些文件）
    resource_param: str | None = None
    # 「説明」「試し」中的说明，如「「{path}」を新規作成します」
    explain: str | None = None
    # 需要的权限：「読み込み」「書き出し」「上書き」（语法设计 17.1）
    permission: str | None = None
    # 使用的规则数据（如「祝日」，或多个：("消込規則", "表記ゆれ規則")）：「説明」中显示其版本（语法设计 14.2）
    uses: str | tuple[str, ...] | None = None

    def param_for(self, particle: str) -> Param | None:
        return next((p for p in self.params if p.particle == particle), None)


@dataclass(frozen=True)
class DateProperty:
    """日付的属性：期日の月末、期日の翌営業日（语法设计 13.5、14.4）"""
    name: str
    function: Callable                # date → 值
    result: Type
    uses: str | None = None           # 使用的规则数据


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[str, VerbSpec] = {}
        self._date_properties: dict[str, DateProperty] = {}
        # 规则数据：名字 → 说明（来源、版本、范围）。「説明」中显示（语法设计 14.2）
        self.rule_data: dict[str, str] = {}
        # 营业日日历：「3営業日」「が休日」由它计算。由日本包提供
        self.calendar = None
        # 其他日历的工厂：「会社」→ 根据执行上下文（设定）生成日历（语法设计 17.3）
        self.calendar_factories: dict[str, Callable] = {}
        self._settings: dict[tuple[str, str], SettingSpec] = {}

    def add_verb(self, spec: VerbSpec) -> None:
        if spec.id in self._verbs:
            raise ValueError(f"動詞が二重に登録されています：{spec.id}")
        if len({p.particle for p in spec.params}) != len(spec.params):
            raise ValueError(f"同じ助詞の引数が二つあります：{spec.id}")
        self._verbs[spec.id] = spec

    def add_setting(self, spec: SettingSpec) -> None:
        self._settings[(spec.namespace, spec.key)] = spec

    def setting(self, namespace: str, key: str) -> SettingSpec | None:
        return self._settings.get((namespace, key))

    def setting_keys(self, namespace: str) -> list[str]:
        return [key for ns, key in self._settings if ns == namespace]

    def has_setting_namespace(self, namespace: str) -> bool:
        return any(ns == namespace for ns, _ in self._settings)

    def add_date_property(self, prop: DateProperty) -> None:
        if prop.name in self._date_properties:
            raise ValueError(f"日付の属性が二重に登録されています：{prop.name}")
        self._date_properties[prop.name] = prop

    def date_property(self, name: str) -> DateProperty | None:
        return self._date_properties.get(name)

    @property
    def date_property_names(self) -> list[str]:
        return list(self._date_properties)

    def verb(self, verb_id: str) -> VerbSpec:
        return self._verbs[verb_id]

    @property
    def verbs(self) -> list[VerbSpec]:
        return list(self._verbs.values())


def default_registry() -> Registry:
    """登记了标准库的注册表"""
    from dandori.stdlib import arithmetic, date_properties, files, japan, table_ops

    registry = Registry()
    arithmetic.register(registry)
    files.register(registry)
    table_ops.register(registry)
    date_properties.register(registry)
    japan.register(registry)
    return registry
