"""计算与显示：足す、引く、掛ける、割る、表示する（语法设计 3.2），
切り捨てる、四捨五入する、切り上げる（语法设计 12.5）

金額与数宽松互通（语法设计 12.4）：有一方是金額，结果就是金額。
结果的类型由 infer 在编译时决定，金額×金額、数÷金額 在编译时报错。
"""

from __future__ import annotations

from functools import reduce

from dandori.frontend.conjugation import Conjugation
from dandori.money import Money, round_number
from dandori.plugin.registry import Effect, Param, Registry, VerbError, VerbSpec
from dandori.runtime import values
from dandori.types import NUMERIC, NUMERIC_LISTS, Type

_SCALAR = NUMERIC                          # 数、金額
_SCALAR_OR_LIST = NUMERIC | NUMERIC_LISTS  # 数、金額、以及它们的並び
_DATE_OR_SCALAR = NUMERIC | {Type.DATE}    # 期日に30日を足す、期日から今日を引く
_OFFSETS = {Type.NUMBER, Type.BUSINESS_DAYS, Type.MONTHS}   # 日付に足せるもの


def _as_list(value) -> list:
    return value if isinstance(value, list) else [value]


def tasu(ctx, numbers, base=None):
    """100と200を足す → 300　／　100に5を足す → 105　／　期日に30日を足す → 日付"""
    values.check_no_blanks(_as_list(numbers))
    if base is not None:
        return reduce(lambda a, b: values.add(a, b, ctx.calendar), _as_list(numbers), base)
    return reduce(values.add, _as_list(numbers), 0)  # 空的列（如筛选后没有行）合计为 0


def hiku(ctx, minuend, subtrahend):
    """10から3を引く → 7"""
    return values.subtract(minuend, subtrahend, ctx.calendar)


def kakeru(ctx, numbers, base=None):
    """2と3を掛ける → 6　／　5に3を掛ける → 15"""
    values.check_no_blanks(_as_list(numbers))
    operands = _as_list(numbers) if base is None else [base, *_as_list(numbers)]
    return reduce(values.multiply, operands, 1)


def waru(ctx, dividend, divisor):
    """10を2で割る → 5"""
    return values.divide(dividend, divisor)


def hyouji(ctx, value):
    """「こんにちは」を表示する。带零头的金額不能显示（语法设计 12.5）"""
    values.check_no_fraction(value)
    ctx.output(values.format_value(value))


# ---------- 结果的类型（编译时） ----------

def _is_money(t) -> bool:
    return t in (Type.MONEY, Type.MONEY_LIST)


def infer_add(args, types, ctx):
    """足す・引く：有一方是金額，结果就是金額。
    日付 ± 数 → 日付；日付 − 日付 → 天数（语法设计 13.3）"""
    found = list(types.values())
    if Type.DATE in found:
        others = [t for t in found if t is not Type.DATE]
        if found.count(Type.DATE) == 2 and "minuend" in types and types["minuend"] is Type.DATE:
            return Type.NUMBER                          # 期日から今日を引く
        if found.count(Type.DATE) == 1 and all(t in _OFFSETS for t in others) and (
                types.get("base") is Type.DATE or types.get("minuend") is Type.DATE):
            return Type.DATE
        raise VerbError("E3045")
    if Type.BUSINESS_DAYS in found or Type.MONTHS in found:
        raise VerbError("E3045")                        # 営業日数・月数只能与日付相加减
    return Type.MONEY if any(_is_money(t) for t in found) else Type.NUMBER


def infer_multiply(args, types, ctx):
    """掛ける：金額×金額 没有意义"""
    money = sum(1 for t in types.values() if _is_money(t))
    if money > 1 or Type.MONEY_LIST in types.values():
        raise VerbError("E3043")
    return Type.MONEY if money else Type.NUMBER


def infer_divide(args, types, ctx):
    """割る：金額÷金額 是比例（数）；数÷金額 没有意义"""
    dividend, divisor = types.get("dividend"), types.get("divisor")
    if divisor is Type.MONEY:
        if dividend is Type.MONEY:
            return Type.NUMBER
        raise VerbError("E3044")
    return dividend if dividend in NUMERIC else None


# ---------- 取整（语法设计 12.5） ----------

def _rounder(mode: str):
    def round_value(ctx, value):
        if isinstance(value, list):
            return [round_value(ctx, v) for v in value]
        if isinstance(value, Money):
            return Money(round_number(value.amount, mode))
        return round_number(value, mode)
    round_value.__doc__ = f"単価×1.1を{mode}（与 Excel 一致）"
    return round_value


def infer_round(args, types, ctx):
    return types.get("value")                      # 取整不改变类型


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec(
        "std.足す", "足す", Conjugation.GODAN,
        params=(Param("を", "numbers", _SCALAR_OR_LIST | _OFFSETS),
                Param("に", "base", _DATE_OR_SCALAR, required=False)),
        returns=Type.NUMBER, impl=tasu, infer=infer_add,
    ))
    registry.add_verb(VerbSpec(
        "std.引く", "引く", Conjugation.GODAN,
        params=(Param("から", "minuend", _DATE_OR_SCALAR),
                Param("を", "subtrahend", _DATE_OR_SCALAR | _OFFSETS)),
        returns=Type.NUMBER, impl=hiku, infer=infer_add,
    ))
    registry.add_verb(VerbSpec(
        "std.掛ける", "掛ける", Conjugation.ICHIDAN,
        params=(Param("を", "numbers", _SCALAR_OR_LIST),
                Param("に", "base", _SCALAR, required=False)),
        returns=Type.NUMBER, impl=kakeru, infer=infer_multiply,
    ))
    registry.add_verb(VerbSpec(
        "std.割る", "割る", Conjugation.GODAN,
        params=(Param("を", "dividend", _SCALAR),
                Param("で", "divisor", _SCALAR)),
        returns=Type.NUMBER, impl=waru, infer=infer_divide,
    ))
    registry.add_verb(VerbSpec(
        "std.表示する", "表示する", Conjugation.SURU,
        params=(Param("を", "value", None),),
        returns=None, impl=hyouji, effect=Effect.OUTPUT,
    ))
    for word, conjugation in (("切り捨てる", Conjugation.ICHIDAN),
                              ("四捨五入する", Conjugation.SURU),
                              ("切り上げる", Conjugation.ICHIDAN)):
        registry.add_verb(VerbSpec(
            f"std.{word}", word, conjugation,
            params=(Param("を", "value", _SCALAR_OR_LIST),),
            returns=None, impl=_rounder(word), infer=infer_round,
        ))
