"""阶段 1 的动词：足す、引く、掛ける、割る、表示する（语法设计 3.2）"""

from __future__ import annotations

from functools import reduce

from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import Param, Registry, VerbSpec
from dandori.runtime import values
from dandori.types import Type

_NUMBER = frozenset({Type.NUMBER})
_NUMBERS = frozenset({Type.NUMBER, Type.NUMBER_LIST})


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def tasu(ctx, numbers, base=None):
    """100と200を足す → 300　／　100に5を足す → 105"""
    values.check_no_blanks(_as_list(numbers))
    operands = _as_list(numbers) if base is None else [base, *_as_list(numbers)]
    return reduce(values.add, operands, 0)          # 空的列（如筛选后没有行）合计为 0


def hiku(ctx, minuend, subtrahend):
    """10から3を引く → 7"""
    return values.subtract(minuend, subtrahend)


def kakeru(ctx, numbers, base=None):
    """2と3を掛ける → 6　／　5に3を掛ける → 15"""
    values.check_no_blanks(_as_list(numbers))
    operands = _as_list(numbers) if base is None else [base, *_as_list(numbers)]
    return reduce(values.multiply, operands, 1)


def waru(ctx, dividend, divisor):
    """10を2で割る → 5"""
    return values.divide(dividend, divisor)


def hyouji(ctx, value):
    """「こんにちは」を表示する"""
    ctx.output(values.format_value(value))


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec(
        "std.足す", "足す", Conjugation.GODAN,
        params=(Param("を", "numbers", _NUMBERS),
                Param("に", "base", _NUMBER, required=False)),
        returns=Type.NUMBER, impl=tasu,
    ))
    registry.add_verb(VerbSpec(
        "std.引く", "引く", Conjugation.GODAN,
        params=(Param("から", "minuend", _NUMBER),
                Param("を", "subtrahend", _NUMBER)),
        returns=Type.NUMBER, impl=hiku,
    ))
    registry.add_verb(VerbSpec(
        "std.掛ける", "掛ける", Conjugation.ICHIDAN,
        params=(Param("を", "numbers", _NUMBERS),
                Param("に", "base", _NUMBER, required=False)),
        returns=Type.NUMBER, impl=kakeru,
    ))
    registry.add_verb(VerbSpec(
        "std.割る", "割る", Conjugation.GODAN,
        params=(Param("を", "dividend", _NUMBER),
                Param("で", "divisor", _NUMBER)),
        returns=Type.NUMBER, impl=waru,
    ))
    registry.add_verb(VerbSpec(
        "std.表示する", "表示する", Conjugation.SURU,
        params=(Param("を", "value", None),),
        returns=None, impl=hyouji,
    ))
