"""入金消込：消し込む（语法设计第 16 节）

    消込の名義は、入金の振込依頼人と請求のフリガナとする。
    消込の金額は、入金の金額と請求の請求金額とする。
    消込の期日は、請求の支払期日とする。          （可选：同额时期日早的优先）
    消込の手数料は、880円までとする。             （可选：容许的手续费差额）
    消込の名義対応は「名義対応.csv」とする。      （可选：振込名義 → 请求一侧的名義）
    入金を請求に消し込んで、結果とする。

原则：只有不会出错的才自动消込，其余交给人确认，但每一行都写明理由。

判定的步骤（对每一笔入金，按入金明细的顺序）：
    1. 名義一致、金額一致                      → 自動消込
    2. 名義一致、差额在手数料以内              → 自動消込（记录差额）
    3. 名義一致、按期日累加的若干张请求书之和一致 → 要確認（まとめ入金の可能性）
    4. 名義一致、金额对不上                    → 要確認（列出候选金额）
    5. 名義不一致、金额与某张请求书一致        → 要確認（列出候选）
    6. 都不一致                                → 入金不明
    最后：剩下的请求书                         → 未入金（已过支払期日时注明）

判定的步骤改变时，VERSION 随之改变（结果会改变，架构设计 7.3）。
"""

from __future__ import annotations

from dandori.connectors import ConnectorError
from dandori.connectors.csv import read_csv
from dandori.frontend.conjugation import Conjugation
from dandori.money import Money
from dandori.plugin.registry import Param, Registry, SettingKind, SettingSpec, VerbError, VerbSpec
from dandori.runtime import values
from dandori.stdlib.files import resolve
from dandori.stdlib.japan.names import normalize_name
from dandori.table.interface import BLANK, Table
from dandori.types import NUMERIC, TableType, Type

VERSION = "第1版"
DESCRIPTION = (f"段取り 消込規則 {VERSION}"
               "（完全一致→手数料差引→まとめ入金→名義のみ一致→金額のみ一致）")

NS = "消込"
_TABLE = frozenset({TableType})


# ---------- 编译时：检查设定，推断结果表的列 ----------

def _setting(ctx_settings, key, required=False):
    value = ctx_settings.get((NS, key))
    if value is None and required:
        raise VerbError("E3053", verb="消し込む", namespace=NS, key=key)
    return value


def _check_column(table: TableType, column: str, label: str, allowed, key: str):
    actual = table.column(column)
    if actual is None:
        raise VerbError("E3025", table=label, column=column, hint=f"列：{'、'.join(table.names)}")
    if actual not in allowed:
        raise VerbError("E3055", namespace=NS, key=key, example=_EXAMPLES[key])


_EXAMPLES = {
    "名義": "「入金の振込依頼人と請求のフリガナ」（どちらも文字列の列）",
    "金額": "「入金の金額と請求の請求金額」（どちらも数か金額の列）",
    "期日": "「請求の支払期日」（日付の列）",
}


def layout(deposits: TableType, invoices: TableType, diff_type: Type):
    """结果表的列：判定、理由、入金一侧的列、请求一侧的列、差額。
    返回 [(结果中的列名, 类型, 来源)]，来源为 ("入金"|"請求", 原列名) 或 None。重名时加前缀"""
    columns = [("判定", Type.STRING, None), ("理由", Type.STRING, None)]
    taken = {"判定", "理由", "差額"}
    for side, table in (("入金", deposits), ("請求", invoices)):
        for name, t in table.columns:
            result_name = f"{side}の{name}" if name in taken else name
            taken.add(result_name)
            columns.append((result_name, t, (side, name)))
    columns.append(("差額", diff_type, None))
    return columns


def infer_reconcile(args, types, ctx):
    names = _setting(ctx.settings, "名義", required=True)
    amounts = _setting(ctx.settings, "金額", required=True)
    due = _setting(ctx.settings, "期日")
    deposits, invoices = types.get("deposits"), types.get("invoices")
    if not isinstance(deposits, TableType) or not isinstance(invoices, TableType):
        return None
    _check_column(deposits, names[0][0], "入金", {Type.STRING}, "名義")
    _check_column(invoices, names[1][0], "請求", {Type.STRING}, "名義")
    _check_column(deposits, amounts[0][0], "入金", NUMERIC, "金額")
    _check_column(invoices, amounts[1][0], "請求", NUMERIC, "金額")
    if due is not None:
        _check_column(invoices, due[0], "請求", {Type.DATE}, "期日")
    diff_type = Type.MONEY if Type.MONEY in (amounts[0][1], amounts[1][1]) else Type.NUMBER
    return TableType(tuple((name, t) for name, t, _ in layout(deposits, invoices, diff_type)))


def check_alias_file(path: str, ctx):
    """名義対応表：编译时读取，检查是两列"""
    _load_alias(path, ctx.base_dir)


def _load_alias(path: str, base_dir) -> dict[str, str]:
    try:
        table = read_csv(resolve(path, base_dir), path)
    except ConnectorError as e:
        raise VerbError(e.code, **e.params) from None
    if len(table.type.columns) != 2 or any(t is not Type.STRING for _, t in table.type.columns):
        raise VerbError("E3056", file=path)
    return {normalize_name(a): normalize_name(b) for a, b in table.rows
            if a is not BLANK and b is not BLANK}


# ---------- 执行时：判定 ----------

def reconcile(ctx, deposits: Table, invoices: Table):
    s = ctx.settings
    (dep_name, _), (inv_name, _) = s[(NS, "名義")]
    (dep_amount, dep_amount_type), (inv_amount, inv_amount_type) = s[(NS, "金額")]
    due = s.get((NS, "期日"))
    limit = s.get((NS, "手数料"))
    alias_path = s.get((NS, "名義対応"))
    alias = _load_alias(alias_path, ctx.base_dir) if alias_path else {}

    diff_type = Type.MONEY if Type.MONEY in (dep_amount_type, inv_amount_type) else Type.NUMBER
    columns = layout(deposits.type, invoices.type, diff_type)
    builder = _ResultBuilder(columns, deposits, invoices)

    # 请求书：按支払期日从早到晚（空欄在最后）；同一天保持原来的顺序
    invoice_rows = list(invoices.records())
    order = list(range(len(invoice_rows)))
    if due is not None:
        order.sort(key=lambda i: (invoice_rows[i].values[due[0]] is BLANK,
                                  invoice_rows[i].values[due[0]] if invoice_rows[i].values[due[0]] is not BLANK
                                  else 0))
    inv_key = {i: _key(invoice_rows[i].values[inv_name], {}) for i in order}
    inv_amt = {i: invoice_rows[i].values[inv_amount] for i in order}
    open_invoices = [i for i in order]                       # 还没有被消掉的请求书（按期日顺序）

    def take(indexes):
        for i in indexes:
            open_invoices.remove(i)

    for deposit in deposits.records():
        amount = deposit.values[dep_amount]
        if amount is BLANK:
            builder.add("入金不明", "金額が空欄です", deposit, None, BLANK)
            continue
        key = _key(deposit.values[dep_name], alias)
        candidates = [i for i in open_invoices if key and inv_key[i] == key and inv_amt[i] is not BLANK]

        # 1. 名義一致、金額一致
        exact = [i for i in candidates if inv_amt[i] == amount]
        if exact:
            chosen = exact[0]
            reason = "名義・金額が一致"
            if len(exact) > 1:
                reason += "（同額の請求が複数あるため、支払期日の早いものを消込）"
            take([chosen])
            builder.add("自動消込", reason, deposit, invoice_rows[chosen], values.subtract(amount, inv_amt[chosen]))
            continue

        # 2. 名義一致、差额在手数料以内（入金比请求少）
        if limit is not None:
            fee = [i for i in candidates if 0 < _amount(values.subtract(inv_amt[i], amount)) <= _amount(limit)]
            if fee:
                chosen = min(fee, key=lambda i: _amount(values.subtract(inv_amt[i], amount)))
                diff = values.subtract(amount, inv_amt[chosen])
                take([chosen])
                builder.add("自動消込", f"名義が一致、手数料差引（{_fmt(values.subtract(0, diff))}）",
                            deposit, invoice_rows[chosen], diff)
                continue

        # 3. 名義一致、按期日从早到晚累加的若干张请求书之和一致
        combined = _prefix_match(candidates, inv_amt, amount, limit)
        if combined:
            parts = "＋".join(_fmt(inv_amt[i]) for i in combined)
            reason = f"名義が一致、請求{len(combined)}件（{parts}）の合計と一致（まとめ入金の可能性）"
            take(combined)
            for i in combined:
                builder.add("要確認", reason, deposit, invoice_rows[i], BLANK)
            continue

        # 4. 名義一致、金额对不上
        if candidates:
            listed = "、".join(_fmt(inv_amt[i]) for i in candidates)
            builder.add("要確認", f"名義は一致するが、金額が一致しません（候補：{listed}）", deposit, None, BLANK)
            continue

        # 5. 名義不一致、金额与某张请求书一致
        same_amount = [i for i in open_invoices if inv_amt[i] is not BLANK and inv_amt[i] == amount]
        if same_amount:
            listed = "、".join(f"{invoice_rows[i].values[inv_name]} {_fmt(inv_amt[i])}" for i in same_amount)
            builder.add("要確認", f"金額のみ一致（候補：{listed}）", deposit, None, BLANK)
            continue

        # 6. 都不一致
        builder.add("入金不明", "名義・金額とも一致する請求がありません", deposit, None, BLANK)

    # 最后：剩下的请求书
    for i in open_invoices:
        reason = "対応する入金がありません"
        if due is not None:
            day = invoice_rows[i].values[due[0]]
            if day is not BLANK and day < ctx.today:
                reason += "（支払期日を過ぎています）"
        builder.add("未入金", reason, None, invoice_rows[i], BLANK)

    return builder.table()


def _key(raw, alias: dict) -> str | None:
    """名義 → 比对用的键：整える，再查名義対応表"""
    if raw is BLANK:
        return None
    key = normalize_name(raw)
    return alias.get(key, key) or None


def _amount(v):
    return v.amount if hasattr(v, "amount") else v


def _fmt(v) -> str:
    """理由中的金额：消込处理的一定是日元，原始数据不带单位时也显示为「440円」"""
    return v.format() if isinstance(v, Money) else Money(v).format()


def _prefix_match(candidates, inv_amt, amount, limit) -> list[int] | None:
    """按期日从早到晚累加，第一次等于入金（或差额在手数料以内）时的那些请求书。至少 2 张"""
    total, chosen = 0, []
    for i in candidates:
        total = values.add(total, inv_amt[i])
        chosen.append(i)
        gap = _amount(values.subtract(total, amount))
        if len(chosen) >= 2 and (gap == 0 or (limit is not None and 0 < gap <= _amount(limit))):
            return chosen
        if gap > (_amount(limit) if limit is not None else 0):
            return None
    return None


class _ResultBuilder:
    def __init__(self, columns, deposits: Table, invoices: Table):
        self.columns = columns
        self.rows: list[tuple] = []
        self.origins: list[str] = []
        display = {("入金", n): d for n, d in zip(deposits.type.names, deposits.display_names)}
        display |= {("請求", n): d for n, d in zip(invoices.type.names, invoices.display_names)}
        self.display_names = []
        for name, _, source in columns:
            if source is None:
                self.display_names.append(name)
            else:
                shown = display[source]
                self.display_names.append(shown if name == source[1] else f"{source[0]}の{shown}")

    def add(self, judgement: str, reason: str, deposit, invoice, diff) -> None:
        row = []
        for name, _, source in self.columns:
            if name == "判定":
                row.append(judgement)
            elif name == "理由":
                row.append(reason)
            elif name == "差額":
                row.append(diff)
            else:
                record = deposit if source[0] == "入金" else invoice
                row.append(BLANK if record is None else record.values[source[1]])
        self.rows.append(tuple(row))
        self.origins.append((deposit or invoice).origin)

    def table(self) -> Table:
        table_type = TableType(tuple((name, t) for name, t, _ in self.columns))
        return Table(table_type, self.rows, self.origins, self.display_names)


def register(registry: Registry) -> None:
    for key, kind, check in (("名義", SettingKind.COLUMN_PAIR, None),
                             ("金額", SettingKind.COLUMN_PAIR, None),
                             ("期日", SettingKind.COLUMN, None),
                             ("手数料", SettingKind.LIMIT, None),
                             ("名義対応", SettingKind.FILE, check_alias_file)):
        registry.add_setting(SettingSpec(NS, key, kind, check))
    registry.rule_data["消込規則"] = DESCRIPTION
    registry.add_verb(VerbSpec(
        "japan.消し込む", "消し込む", Conjugation.GODAN,
        params=(Param("を", "deposits", _TABLE), Param("に", "invoices", _TABLE)),
        returns=None, impl=reconcile, infer=infer_reconcile, uses=("消込規則", "表記ゆれ規則"),
    ))
