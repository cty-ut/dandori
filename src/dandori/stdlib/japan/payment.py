"""締め・支払（语法设计 14.7）

支払条件：「〈締め〉締め〈支払月〉〈支払日〉払い」
    月末締め翌月末払い、20日締め翌月10日払い、月末締め翌々月15日払い、15日締め当月末払い

只算出原始的支付日。正好是休日时怎么处理（前倒し・後倒し）因公司而异，由用户明确写出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from dandori.dates import add_months, month_end
from dandori.frontend.normalize import normalize

_TERMS = re.compile(
    r"(?:(?P<close_end>月?末)|(?P<close_day>\d{1,2})日)締め"
    r"(?P<month>当月|翌月|翌々月|(?P<after>\d{1,2})(?:か月|ヶ月|カ月|ヵ月|ケ月)後)"
    r"(?:(?P<pay_end>月?末)|(?P<pay_day>\d{1,2})日)払い"
)
_MONTH_OFFSETS = {"当月": 0, "翌月": 1, "翌々月": 2}


@dataclass(frozen=True)
class PaymentTerms:
    close_day: int | None     # None：月末締め
    month_offset: int
    pay_day: int | None       # None：月末払い

    def payment_date(self, d: date) -> date:
        # 1. 交易日不晚于当月的締め日 → 当月締め；晚于 → 次月締め
        closing = d.replace(day=1)
        if self.close_day is not None and d.day > self.close_day:
            closing = add_months(closing, 1)
        # 2. 支付月 ＝ 締め的月份 ＋ 偏移
        month = add_months(closing, self.month_offset)
        # 3. 支付日；该月没有这一天时取月末
        end = month_end(month)
        if self.pay_day is None:
            return end
        return month.replace(day=min(self.pay_day, end.day))


def parse_terms(text: str) -> PaymentTerms | None:
    m = _TERMS.fullmatch(normalize(text).replace(" ", "").replace("　", ""))
    if m is None:
        return None
    close_day = int(m["close_day"]) if m["close_day"] else None
    pay_day = int(m["pay_day"]) if m["pay_day"] else None
    if (close_day is not None and not 1 <= close_day <= 31) or (pay_day is not None and not 1 <= pay_day <= 31):
        return None
    offset = int(m["after"]) if m["after"] else _MONTH_OFFSETS[m["month"]]
    return PaymentTerms(close_day, offset, pay_day)
