"""日本包：日本的业务知识（语法设计 14.1）

语言核心只提供机制（日付、带单位的数、「の」取属性），这里提供知识：
祝日表、银行营业日、「翌営業日」「前倒しする」等。
以后支持其他国家、行业时，换一个包即可。
"""

from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import DateProperty, Param, Registry, VerbSpec
from dandori.stdlib.japan.calendar import load_bank_calendar
from dandori.types import Type

_DATE = frozenset({Type.DATE})


def register(registry: Registry) -> None:
    calendar = load_bank_calendar()
    registry.calendar = calendar
    registry.rule_data["祝日"] = calendar.description

    registry.add_date_property(DateProperty("翌営業日", calendar.next, Type.DATE, uses="祝日"))
    registry.add_date_property(DateProperty("前営業日", calendar.previous, Type.DATE, uses="祝日"))

    # 是休日就提前（推迟）到最近的营业日；是营业日就不变（经理部门的「前倒し」「後倒し」）
    for word, function in (("前倒しする", calendar.backward), ("後倒しする", calendar.forward)):
        registry.add_verb(VerbSpec(
            f"japan.{word}", word, Conjugation.SURU,
            params=(Param("を", "day", _DATE),),
            returns=Type.DATE, impl=lambda ctx, day, f=function: f(day), uses="祝日",
        ))
