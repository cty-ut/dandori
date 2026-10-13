"""注册表（架构设计 5.1、6）

动词定义是"唯一的事实来源"：词典、参数检查、执行都从这里派生。
标准库也通过这个注册表登记，和第三方插件走同一条路，没有特权后门。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dandori.frontend.conjugation import Conjugation
from dandori.types import Type, ValueType


@dataclass(frozen=True)
class Param:
    """参数槽：用助词标记（语法设计 3.2）"""

    particle: str                       # 助词，如「を」
    name: str                           # 实现函数的参数名
    accepts: frozenset[Type] | None     # 接受的类型；None 表示什么都接受（如「表示する」）
    required: bool = True


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
    # 结果的类型取决于参数时（如「読み込む」：取决于文件的列），在编译时调用：
    # infer(参数名 → 语法树, InferContext) → 类型。出错时抛出 VerbError
    infer: Callable | None = None

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
    from dandori.stdlib import arithmetic, files

    registry = Registry()
    arithmetic.register(registry)
    files.register(registry)
    return registry
