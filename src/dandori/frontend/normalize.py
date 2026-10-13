"""正規化（语法设计 1.8）

在断词之前，把全角数字、全角记号等统一成半角，后面的断词器就只需要处理一种写法。

三条规则：
1. 只做"一个字符换成一个字符"的替换，所以正規化前后长度相同、位置一一对应。
   这样报错时，同一个下标可以直接指回用户写的原文。
2. 「」里面是用户的数据（比如「１００円」），原样保留，绝不替换。
3. 「ー」（片假名长音符）永远不替换，否则会误伤「データ」这样的词。
"""

_FULLWIDTH_DIGITS = "０１２３４５６７８９"
_DIGITS = set("0123456789" + _FULLWIDTH_DIGITS)

_TABLE = {
    **{fw: str(i) for i, fw in enumerate(_FULLWIDTH_DIGITS)},
    # 全角英文字母：ＩＤ 和 ID 是同一个名字
    **{chr(0xFF21 + i): chr(ord("A") + i) for i in range(26)},
    **{chr(0xFF41 + i): chr(ord("a") + i) for i in range(26)},
    "＿": "_",
    "＋": "+",
    "－": "-",
    "＊": "*", "×": "*",
    "／": "/", "÷": "/",
    "（": "(",
    "）": ")",
    "＜": "<",
    "＞": ">",
    "＝": "=",
    "！": "!",
}


def normalize(text: str) -> str:
    out = []
    in_string = False

    for i, ch in enumerate(text):
        if in_string:
            out.append(ch)
            # 字符串不能跨行（语法设计 1.3），遇到换行也视为结束
            if ch in "」\n":
                in_string = False
        elif ch == "「":
            in_string = True
            out.append(ch)
        elif ch == "．" and _is_digit(text, i - 1) and _is_digit(text, i + 1):
            # 全角句点只有夹在数字中间时才是小数点：３．１４
            out.append(".")
        else:
            out.append(_TABLE.get(ch, ch))

    result = "".join(out)
    assert len(result) == len(text), "正規化は文字数を変えてはいけない"
    return result


def _is_digit(text: str, i: int) -> bool:
    return 0 <= i < len(text) and text[i] in _DIGITS
