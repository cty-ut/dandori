"""注册表（架构设计 5.1、6）

动词定义是"唯一的事实来源"：词典、参数检查、说明文字都从这里派生。
标准库也通过这个注册表登记，和第三方插件走同一条路，没有特权后门。

阶段 1 第 1 步只需要辞书形和活用类型（用来生成词典）。
参数槽、类型、实现函数等字段在之后的步骤中加入。
"""

from __future__ import annotations

from dataclasses import dataclass

from dandori.frontend.conjugation import Conjugation


@dataclass(frozen=True)
class VerbSpec:
    id: str                    # 完整标识，如 "std.足す"
    dictionary_form: str       # 辞书形，如 "足す"
    conjugation: Conjugation   # 活用类型


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[str, VerbSpec] = {}

    def add_verb(self, spec: VerbSpec) -> None:
        if spec.id in self._verbs:
            raise ValueError(f"動詞が二重に登録されています：{spec.id}")
        self._verbs[spec.id] = spec

    @property
    def verbs(self) -> list[VerbSpec]:
        return list(self._verbs.values())


def default_registry() -> Registry:
    """登记了标准库的注册表"""
    from dandori.stdlib import arithmetic

    registry = Registry()
    arithmetic.register(registry)
    return registry
