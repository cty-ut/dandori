"""日付的通用属性：年・月・日・曜日・月初・月末・年度・和暦（语法设计 13.5）

通过注册表登记，与日本包登记「翌営業日」等走同一条路。
"""

from dandori import dates
from dandori.plugin.registry import DateProperty, Registry
from dandori.types import Type


def register(registry: Registry) -> None:
    for name, function, result in (
        ("年", lambda d: d.year, Type.NUMBER),
        ("月", lambda d: d.month, Type.NUMBER),
        ("日", lambda d: d.day, Type.NUMBER),
        ("曜日", dates.weekday, Type.STRING),
        ("月初", dates.month_start, Type.DATE),
        ("月末", dates.month_end, Type.DATE),
        ("年度", dates.fiscal_year, Type.NUMBER),        # 4 月开始
        ("和暦", dates.format_wareki, Type.STRING),
    ):
        registry.add_date_property(DateProperty(name, function, result))
