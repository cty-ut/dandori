"""名字的规范化：整える（语法设计第 15 节）

把同一家公司的不同写法统一成同一个"用来比对的文字列"：

    山田商事株式会社          → 山田商事
    ｶ)ﾔﾏﾀﾞｼﾖｳｼﾞ（银行明细）   → ヤマダシヨウジ
    ヤマダショウジ（フリガナ） → ヤマダシヨウジ

全銀フォーマット（银行间转账的标准格式）只能用半角片假名、不能用小字假名、
法人格用缩写（ｶ) ＝ 株式会社）。这里的规则就是为了和它对齐。

规则改变会改变照合的结果，所以规则有版本（VERSION），「説明」中显示。
"""

from __future__ import annotations

import re
import unicodedata

VERSION = "第1版"

# 法人格的全称（汉字）
_LEGAL_FULL = (
    "特定非営利活動法人", "社会福祉法人", "一般社団法人", "一般財団法人", "公益社団法人", "公益財団法人",
    "独立行政法人", "医療法人社団", "医療法人財団", "弁護士法人", "税理士法人", "司法書士法人",
    "株式会社", "有限会社", "合同会社", "合名会社", "合資会社", "医療法人", "社団法人", "財団法人",
    "学校法人", "宗教法人", "監査法人", "NPO法人",
)
# 法人格的括号写法（NFKC 之后 ㈱ 变成 (株)）
_LEGAL_SHORT = ("株", "有", "同", "名", "資", "医", "社", "財", "学", "宗", "福")
# 银行的法人略語（全銀協）：前株「カ)」、後株「(カ」、「(カ)」
_LEGAL_BANK = ("トクヒ", "シユウ", "シヤ", "ザイ", "ガク", "フク", "ドク", "ベン", "ゼイ", "カ", "ユ", "ド", "メ",
               "シ", "イ")
# 法人格的假名全称（小字假名已转为大字之后）
_LEGAL_KANA = ("カブシキガイシヤ", "カブシキカイシヤ", "ユウゲンガイシヤ", "ユウゲンカイシヤ",
               "ゴウドウガイシヤ", "ゴウドウカイシヤ")

# 常见异体字 → 通用字（人名、公司名中常见；旧系统写不出这些字，同一个人在不同系统中写法不同）
_VARIANTS = str.maketrans({
    "髙": "高", "﨑": "崎", "嵜": "崎", "邊": "辺", "邉": "辺", "齋": "斎", "齊": "斎",
    "濵": "浜", "濱": "浜", "德": "徳", "冨": "富", "桒": "桑", "嶋": "島", "嶌": "島",
    "廣": "広", "澤": "沢", "櫻": "桜", "國": "国", "眞": "真", "惠": "恵", "實": "実",
    "藏": "蔵", "槇": "槙", "瀨": "瀬", "條": "条", "淺": "浅", "龍": "竜", "釼": "剣",
})
VARIANT_COUNT = len(_VARIANTS)

_SMALL_KANA = str.maketrans("ァィゥェォッャュョヮヵヶ", "アイウエオツヤユヨワカケ")
_HYPHENS = str.maketrans({c: "ー" for c in "-‐‑‒–—―−ｰ"})

DESCRIPTION = (f"段取り 表記ゆれ規則 {VERSION}"
               f"（法人格 {len(_LEGAL_FULL) + len(_LEGAL_BANK)}種、異体字 {VARIANT_COUNT}字）")

_FULL = re.compile("|".join(sorted(_LEGAL_FULL, key=len, reverse=True)))
_SHORT = re.compile(r"\((?:" + "|".join(_LEGAL_SHORT) + r")\)")
_BANK_ALT = "|".join(sorted(_LEGAL_BANK, key=len, reverse=True))
_BANK = re.compile(rf"^\((?:{_BANK_ALT})\)|^(?:{_BANK_ALT})\)|\((?:{_BANK_ALT})\)$|\((?:{_BANK_ALT})$")
_KANA = re.compile("|".join(_LEGAL_KANA))


def normalize_name(text: str) -> str:
    # 1. 兼容字符统一：半角片假名 → 全角（浊音合并）、全角英数 → 半角、㈱ → (株)
    s = unicodedata.normalize("NFKC", text)
    # 2. 英文大写
    s = s.upper()
    # 先去掉空格：银行明细中常有「ｶ) ﾔﾏﾀﾞ」这样的空格，会妨碍识别缩写的位置
    s = re.sub(r"\s+", "", s)
    # 3. 去掉法人格：全称、括号写法、银行缩写（只在开头、结尾或带完整括号时）
    s = _FULL.sub("", s)
    s = _SHORT.sub("", s)
    s = _BANK.sub("", s)
    # 4. 平假名 → 片假名
    s = "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)
    # 5. 小字假名 → 大字（全銀フォーマット不能用小字假名）
    s = s.translate(_SMALL_KANA)
    s = _KANA.sub("", s)                 # 假名全称的法人格（大字化之后才能统一识别）
    # 6. 长音、连字符统一为「ー」。长音只在片假名之后才有意义，其他位置的连字符是符号，去掉：
    #    シーシー 不变；ABC-JAPAN 与 ABC JAPAN 都变成 ABCJAPAN
    s = s.translate(_HYPHENS)
    s = "".join(c for i, c in enumerate(s) if c != "ー" or (i > 0 and "ァ" <= s[i - 1] <= "ヺ"))
    # 7. 常见异体字统一
    s = s.translate(_VARIANTS)
    # 8. 只保留文字和数字（去掉符号）
    return "".join(c for c in s if c == "ー" or unicodedata.category(c)[0] in "LN")
