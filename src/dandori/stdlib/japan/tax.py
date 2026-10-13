"""消費税率（语法设计 14.8）

税率由交易日决定：处理旧发票时自动使用当时的税率。
这是规则数据：将来法律修改时更新这张表。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from dandori.dates import format_western
from dandori.plugin.registry import VerbError

# (适用开始日, 标准税率, 軽減税率)。軽減税率为 None：当时没有軽減税率，与标准税率相同
_RATES = (
    (date(2019, 10, 1), Decimal("0.10"), Decimal("0.08")),
    (date(2014, 4, 1), Decimal("0.08"), None),
    (date(1997, 4, 1), Decimal("0.05"), None),
    (date(1989, 4, 1), Decimal("0.03"), None),
)

DESCRIPTION = "消費税法（3%：1989年4月〜、5%：1997年4月〜、8%：2014年4月〜、10%・軽減8%：2019年10月〜）"


def _rates(d: date):
    for start, standard, reduced in _RATES:
        if d >= start:
            return standard, reduced if reduced is not None else standard
    raise VerbError("E5014", date=format_western(d))


def standard_rate(d: date) -> Decimal:
    return _rates(d)[0]


def reduced_rate(d: date) -> Decimal:
    return _rates(d)[1]
