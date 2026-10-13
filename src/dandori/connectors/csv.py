"""CSV 连接器（语法设计 8.6）

日本的 CSV 有几个特有的问题，都在这里处理：
- 文字编码：Excel 日文环境默认存为 Shift_JIS，银行 CSV 也大多如此 → 自动识别
- 数字：千位分隔符「1,000」、全角数字「１００」 → 都识别为数
- 编码：以 0 开头的「00123」「0600001」（取引先コード、郵便番号、電話番号） → 文字列，保留开头的 0
- 金額：「1,000円」「¥1,000」；会计的负数写法「△1,000」「▲1,000」（语法设计 12.3）
- 日付：2026/10/31、2026-10-31、令和8年10月31日、R8.10.31 等（语法设计 13.8）
- 空格子 → 空欄（不是 0）
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal
from pathlib import Path

from datetime import date

from dandori import dates
from dandori.connectors import ConnectorError
from dandori.frontend.normalize import normalize
from dandori.money import Money
from dandori.table.interface import BLANK, Table
from dandori.types import TableType, Type

_NUMBER = re.compile(r"-?(\d+|\d{1,3}(,\d{3})+)(\.\d+)?")
# 以 0 开头、后面还跟着数字的：编码，不是数（「0」本身和「0.5」除外）
_CODE = re.compile(r"-?0[\d,]")


def read_csv(path: Path, display_name: str) -> Table:
    """读取 CSV。display_name 是程序里写的文件名，用于错误信息"""
    if not path.is_file():
        raise ConnectorError("E3019", file=display_name)

    text = _decode(path.read_bytes(), display_name)
    records = list(csv.reader(io.StringIO(text)))
    if not records or not any(cell.strip() for cell in records[0]):
        raise ConnectorError("E3028", file=display_name)

    header = [cell.strip() for cell in records[0]]
    names = _column_names(header, display_name)

    raw_rows, origins = [], []
    for line_no, record in enumerate(records[1:], start=2):
        if not any(cell.strip() for cell in record):
            continue                                   # 完全空白的行跳过
        if len(record) != len(header):
            raise ConnectorError("E3021", file=display_name, row=str(line_no))
        raw_rows.append([cell.strip() for cell in record])
        origins.append(f"{display_name} の {line_no}行目")

    # 类型推断：一列中所有非空的值都是数 → 数；否则 → 文字列。
    # 只要有一个值像编码（以 0 开头，如「00123」），整列就是文字列：
    # 真正的金额、数量不会写成「0123」，而编码的开头 0 一旦丢失就无法恢复。
    # 是数、并且有一个值带「円」或「¥」 → 整列是金額（不带单位的值也视为日元）
    types = []
    for i in range(len(header)):
        cells = [row[i] for row in raw_rows if row[i] != ""]
        parsed = [_parse_number(c) for c in cells]
        is_number = (cells and all(p is not None for p in parsed)
                     and not any(_CODE.match(_strip_sign(normalize(c))) for c in cells))
        if not is_number:
            is_date = cells and all(_parse_date(c, display_name, raw_rows, i) is not None for c in cells)
            types.append(Type.DATE if is_date else Type.STRING)
        elif any(isinstance(p, Money) for p in parsed):
            types.append(Type.MONEY)
        else:
            types.append(Type.NUMBER)

    rows = [tuple(_convert(cell, t) for cell, t in zip(row, types)) for row in raw_rows]
    table_type = TableType(tuple(zip(names, types)))
    return Table(table_type, rows, origins, header)


def _decode(data: bytes, display_name: str) -> str:
    """先试 UTF-8（含 BOM），再试 Shift_JIS（CP932：Windows 实际使用的 Shift_JIS）"""
    for encoding in ("utf-8-sig", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ConnectorError("E3020", file=display_name)


def _column_names(header: list[str], display_name: str) -> list[str]:
    names = []
    for i, cell in enumerate(header, start=1):
        if cell == "":
            raise ConnectorError("E3023", file=display_name, col=str(i))
        name = normalize(cell)                         # ＩＤ 和 ID 是同一列
        if name in names:
            raise ConnectorError("E3022", file=display_name, name=cell)
        names.append(name)
    return names


def _strip_sign(text: str) -> str:
    return text[1:] if text[:1] in "-△▲" else text


def _parse_number(cell: str):
    """数或金額：1,000、△1,000、1,000円、¥1,000。不是数时返回 None"""
    text = normalize(cell)                             # 全角数字、全角负号、￥ → 半角
    negative = text[:1] in "-△▲"                       # △▲：日本会计的负数写法
    text = _strip_sign(text)
    money = False
    if text.startswith("¥"):
        money, text = True, text[1:]
    if text.endswith("円"):
        money, text = True, text[:-1]
    if not _NUMBER.fullmatch(text) or text.startswith("-"):
        return None
    text = text.replace(",", "")
    value = Decimal(text) if "." in text else int(text)
    if isinstance(value, Decimal) and value == value.to_integral_value():
        value = int(value)                             # 3.0 → 3，与计算结果一致
    if negative:
        value = -value
    return Money(value) if money else value


def _parse_date(cell: str, display_name: str, raw_rows, column: int):
    """日付，或 None。形式上是日期但不存在时，报错并指出行号（语法设计 13.8）"""
    try:
        return dates.parse_cell(normalize(cell))
    except dates.InvalidDate as e:
        row = next(i for i, r in enumerate(raw_rows, start=2) if r[column] == cell)
        hint = f"（{e.hint}）" if e.hint else ""
        raise ConnectorError("E3050", file=display_name, row=str(row), value=cell, hint=hint) from None


def _convert(cell: str, column_type: Type):
    if cell == "":
        return BLANK
    if column_type is Type.DATE:
        return dates.parse_cell(normalize(cell))
    if column_type is Type.MONEY:
        value = _parse_number(cell)
        return value if isinstance(value, Money) else Money(value)
    if column_type is Type.NUMBER:
        return _parse_number(cell)
    return cell


# ---------- 写出（语法设计 11.3） ----------

def write_csv(table: Table) -> bytes:
    """表 → CSV 的字节

    UTF-8 带 BOM：不带 BOM 时用 Excel 双击打开会乱码。换行用 CRLF（Windows / Excel 的标准）。
    数不加千位分隔符，Excel 打开后仍然是数；空欄写成空格子。
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(table.display_names)
    for row in table.rows:
        writer.writerow([_format_cell(v) for v in row])
    return buffer.getvalue().encode("utf-8-sig")


def _format_cell(value) -> str:
    if value is BLANK:
        return ""
    if isinstance(value, Money):
        value = value.amount                           # 不带单位：Excel 打开后仍是数
    if isinstance(value, date):
        return dates.format_csv(value)                 # 2026/10/31：Excel 识别为日期
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return str(value)
