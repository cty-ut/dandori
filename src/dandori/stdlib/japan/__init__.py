"""日本包：日本的业务知识（语法设计 14.1）

语言核心只提供机制（日付、带单位的数、「の」取属性），这里提供知识：
祝日表、银行营业日、「翌営業日」「前倒しする」、支払条件、消費税率等。
以后支持其他国家、行业时，换一个包即可。
"""

from dandori.frontend import ast
from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import (DateProperty, Param, Registry, SettingKind, SettingSpec, VerbError,
                                     VerbSpec)
from dandori.stdlib.japan import names, reconcile, tax
from dandori.table.interface import BLANK
from dandori.stdlib.japan.calendar import load_bank_calendar
from dandori.stdlib.japan.payment import parse_terms
from dandori.stdlib.japan.schedule import parse_schedule
from dandori.types import Type

_DATE = frozenset({Type.DATE})
_STRING = frozenset({Type.STRING})
_STRINGS = frozenset({Type.STRING, Type.STRING_LIST})


def check_company_holidays(path: str, ctx):
    """「暦の会社休日」：编译时读取，第一列必须是日付"""
    _company_days(path, ctx.base_dir, ctx.notes)


def _company_days(path: str, base_dir, notes: dict | None = None):
    """会社休日的文件：CSV 或 Excel（可以带表名）"""
    from dandori.stdlib.files import load_rule_file
    table = load_rule_file(path, base_dir, notes)
    if table.type.columns[0][1] is not Type.DATE:
        raise VerbError("E3061", file=path)
    return table, {row[0] for row in table.rows if row[0] is not BLANK}


def check_schedule(text: str, ctx):
    """「実行の予定」：编译时检查写法（平台设计 3）。用到营业日时，「説明」中显示祝日表"""
    schedule = parse_schedule(text)
    if schedule.uses_company and ("暦", "会社休日") not in ctx.settings:
        raise VerbError("E3060", word="会社営業日")
    return "祝日" if schedule.uses_calendar else None


def check_payment(args, types, ctx):
    """支払条件直接写在程序中时，编译时检查写法"""
    terms = args.get("terms")
    if isinstance(terms, ast.String) and parse_terms(terms.value) is None:
        raise VerbError("E3052", terms=terms.value)


def totonoeru(ctx, text):
    """「ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ」を整える → ヤマダシヨウジ（语法设计第 15 节）。空欄保持为空欄"""
    if isinstance(text, list):
        return [v if v is BLANK else names.normalize_name(v) for v in text]
    return names.normalize_name(text)


def payment_day(ctx, day, terms):
    """請求日を「月末締め翌月末払い」で支払日にする（只算原始日期）"""
    parsed = parse_terms(terms)
    if parsed is None:                       # 来自 CSV 的列等：执行时检查
        raise VerbError("E5013", terms=terms)
    return parsed.payment_date(day)


def register(registry: Registry) -> None:
    calendar = load_bank_calendar()
    registry.calendar = calendar
    registry.rule_data["祝日"] = calendar.description

    # 公司日历（语法设计 17.3）：暦の会社休日は「会社休日.csv」とする。
    registry.add_setting(SettingSpec("暦", "会社休日", SettingKind.FILE, check_company_holidays))

    def company_calendar(ctx):
        from dandori.stdlib.files import check_permission, record_rule_read
        path = ctx.settings[("暦", "会社休日")]
        check_permission(ctx, "読み込み", path)
        table, days = _company_days(path, ctx.base_dir)
        record_rule_read(ctx, path, len(table))
        return calendar.with_company_holidays(days)

    registry.calendar_factories["会社"] = company_calendar

    # 定时执行（平台设计 3）：语言只检查和说明，按时运行的是平台
    registry.add_setting(SettingSpec("実行", "予定", SettingKind.TEXT, check_schedule, example="「毎月25日 9時」"))

    registry.add_date_property(DateProperty("翌営業日", calendar.next, Type.DATE, uses="祝日"))
    registry.add_date_property(DateProperty("前営業日", calendar.previous, Type.DATE, uses="祝日"))

    registry.add_verb(VerbSpec(
        "japan.支払日にする", "支払日にする", Conjugation.SURU,
        params=(Param("を", "day", _DATE), Param("で", "terms", _STRING)),
        returns=Type.DATE, impl=payment_day, check=check_payment,
    ))

    registry.rule_data["表記ゆれ規則"] = names.DESCRIPTION
    registry.add_verb(VerbSpec(
        "japan.整える", "整える", Conjugation.ICHIDAN,
        params=(Param("を", "text", _STRINGS),),
        returns=None, impl=totonoeru, infer=lambda args, types, ctx: types.get("text"),
        uses="表記ゆれ規則",
    ))

    reconcile.register(registry)

    registry.rule_data["消費税率"] = tax.DESCRIPTION
    registry.add_date_property(DateProperty("標準税率", tax.standard_rate, Type.NUMBER, uses="消費税率"))
    registry.add_date_property(DateProperty("軽減税率", tax.reduced_rate, Type.NUMBER, uses="消費税率"))

    # 是休日就提前（推迟）到最近的营业日；是营业日就不变（经理部门的「前倒し」「後倒し」）
    def maedaoshi(ctx, day):
        """支払日を前倒しする（休日なら前の営業日、営業日ならそのまま）"""
        return calendar.backward(day)

    def atodaoshi(ctx, day):
        """支払日を後倒しする（休日なら次の営業日、営業日ならそのまま）"""
        return calendar.forward(day)

    for word, function in (("前倒しする", maedaoshi), ("後倒しする", atodaoshi)):
        registry.add_verb(VerbSpec(
            f"japan.{word}", word, Conjugation.SURU,
            params=(Param("を", "day", _DATE),),
            returns=Type.DATE, impl=function, uses="祝日",
        ))
