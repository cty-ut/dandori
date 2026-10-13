"""注册表（架构设计 5.1、6）

动词定义是"唯一的事实来源"：词典、参数检查、执行都从这里派生。
标准库也通过这个注册表登记，和第三方插件走同一条路，没有特权后门。
"""

from __future__ import annotations

from dataclasses import dataclass
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

    def param_for(self, particle: str) -> Param | None:
        return next((p for p in self.params if p.particle == particle), None)


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[str, VerbSpec] = {}

    def add_verb(self, spec: VerbSpec) -> None:
        if spec.id in self._verbs:
            raise ValueError(f"動詞が二重に登録されています：{spec.id}")
        if len({p.particle for p in spec.params}) != len(spec.params):
            raise ValueError(f"同じ助詞の引数が二つあります：{spec.id}")
        self._verbs[spec.id] = spec

    def verb(self, verb_id: str) -> VerbSpec:
        return self._verbs[verb_id]

    @property
    def verbs(self) -> list[VerbSpec]:
        return list(self._verbs.values())


def default_registry() -> Registry:
    """登记了标准库的注册表"""
    from dandori.stdlib import arithmetic, files, table_ops

    registry = Registry()
    arithmetic.register(registry)
    files.register(registry)
    table_ops.register(registry)
    return registry
