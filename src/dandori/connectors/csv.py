"""CSV 连接器（语法设计 8.6）

日本的 CSV 有几个特有的问题，都在这里处理：
- 文字编码：Excel 日文环境默认存为 Shift_JIS，银行 CSV 也大多如此 → 自动识别
- 数字：千位分隔符「1,000」、全角数字「１００」 → 都识别为数
- 编码：以 0 开头的「00123」「0600001」（取引先コード、郵便番号、電話番号） → 文字列，保留开头的 0
- 空格子 → 空欄（不是 0）
"""

from __future__ import annotations

import csv
import io
import re
from decimal import Decimal
from pathlib import Path

from dandori.connectors import ConnectorError
from dandori.frontend.normalize import normalize
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
    # 真正的金额、数量不会写成「0123」，而编码的开头 0 一旦丢失就无法恢复
    types = []
    for i in range(len(header)):
        cells = [row[i] for row in raw_rows if row[i] != ""]
        is_number = (cells and all(_parse_number(c) is not None for c in cells)
                     and not any(_CODE.match(normalize(c)) for c in cells))
        types.append(Type.NUMBER if is_number else Type.STRING)

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


def _parse_number(cell: str):
    text = normalize(cell)                             # 全角数字、全角负号 → 半角
    if not _NUMBER.fullmatch(text):
        return None
    text = text.replace(",", "")
    if "." not in text:
        return int(text)
    value = Decimal(text)
    return int(value) if value == value.to_integral_value() else value   # 3.0 → 3，与计算结果一致


def _convert(cell: str, column_type: Type):
    if cell == "":
        return BLANK
    if column_type is Type.NUMBER:
        return _parse_number(cell)
    return cell
