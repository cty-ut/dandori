"""阶段 9-1：读取 Excel（语法设计 19）"""

import re
import zipfile
from datetime import datetime, time
from decimal import Decimal

import openpyxl
import pytest

from dandori.api import Context, Source, compile_source, run
from dandori.money import Money
from dandori.table.interface import BLANK


def book(path, sheets: dict, formats: dict | None = None, merge: dict | None = None):
    """sheets：表名 → 行的列表。formats：(表名, 单元格) → 格式。merge：表名 → 合并的范围"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
        for (sheet, cell), fmt in (formats or {}).items():
            if sheet == name:
                ws[cell].number_format = fmt
        for area in (merge or {}).get(name, []):
            ws.merge_cells(area)
    wb.save(path)
    return path


def cache_formula_results(path, results: dict):
    """openpyxl 不计算公式：模拟"用 Excel 保存过"，把计算结果写进文件。results：单元格 → 值"""
    with zipfile.ZipFile(path) as z:
        files = {n: z.read(n) for n in z.namelist()}
    for name in files:
        if name.startswith("xl/worksheets/sheet"):
            xml = files[name].decode("utf-8")
            for cell, value in results.items():
                xml = re.sub(rf'(<c r="{cell}"[^>]*>)<f>([^<]*)</f><v\s*/?>(</v>)?',
                             rf"\g<1><f>\g<2></f><v>{value}</v>", xml)
            files[name] = xml.encode("utf-8")
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)


def run_code(code, folder):
    output = []
    errors = run(Source(code), Context(output=output.append, base_dir=folder))
    return output, [e.code for e in errors]


def codes(code, folder):
    return [e.code for e in compile_source(Source(code), base_dir=folder).errors]


def table_of(code, folder):
    """程序中最后读取的表（编译时的类型和执行时的值）"""
    result = compile_source(Source(code), base_dir=folder)
    assert result.errors == [], [e.message for e in result.errors]
    from dandori.stdlib.files import load_table
    import re as _re
    m = _re.search(r"「([^」]+)」(?:の「([^」]+)」シート)?を読み込", code)
    return load_table(m.group(1), folder, m.group(2))[0], result


@pytest.fixture
def invoices(tmp_path):
    book(tmp_path / "請求一覧.xlsx", {
        "10月": [["請求一覧（2026年10月）"], [],
                 ["請求番号", "取引先", "請求金額", "支払期日", "コード"],
                 ["A001", "山田商事", 109560, datetime(2026, 10, 31), "00123"],
                 ["A002", "佐藤物産", 66000, datetime(2026, 11, 30), "00456"]],
        "11月": [["請求番号", "金額"], ["B001", 1000]],
    }, formats={("10月", "C4"): '"¥"#,##0', ("10月", "C5"): "#,##0円"})
    return tmp_path


# ---------- 表 ----------

def test_read_sheet(invoices):
    code = "「請求一覧.xlsx」の「10月」シートを読み込んで、請求とする。\n請求の請求金額を合計して表示する。"
    assert run_code(code, invoices) == (["175,560円"], [])
    table, result = table_of(code, invoices)
    assert [t.value for _, t in table.type.columns] == ["文字列", "文字列", "金額", "日付", "文字列"]
    assert table.rows[0][2] == Money(109560) and table.rows[0][4] == "00123"
    assert table.origins[0] == "請求一覧.xlsx（10月） の 4行目"     # Excel 的行号
    assert result.resources[0].explain == "「請求一覧.xlsx」の「10月」シートを読み込みます（見出しは3行目）"


def test_sheet_required_when_several(invoices):
    assert codes("「請求一覧.xlsx」を読み込む。", invoices) == ["E3071"]
    assert codes("「請求一覧.xlsx」の「12月」シートを読み込む。", invoices) == ["E3072"]


def test_single_sheet_may_be_omitted(tmp_path):
    book(tmp_path / "明細.xlsx", {"明細": [["取引先", "金額"], ["山田商事", 1000]]})
    result = compile_source(Source("「明細.xlsx」を読み込む。"), base_dir=tmp_path)
    assert result.errors == []
    assert result.resources[0].explain == "「明細.xlsx」を読み込みます（「明細」シート）"


def test_fullwidth_sheet_name(invoices):
    assert codes("「請求一覧.xlsx」の「１０月」シートを読み込む。", invoices) == []


def test_ambiguous_fullwidth_sheets_are_not_guessed(tmp_path):
    book(tmp_path / "a.xlsx", {"10月": [["a", "b"], [1, 2]], "１０月": [["a", "b"], [1, 2]]})
    assert codes("「a.xlsx」の「10月」シートを読み込む。", tmp_path) == []          # 完全一致
    book(tmp_path / "b.xlsx", {"10月": [["a", "b"], [1, 2]], "１０月": [["a", "b"], [1, 2]], "x": [["a"]]})
    assert codes("「b.xlsx」の「１0月」シートを読み込む。", tmp_path) == ["E3072"]  # 两张都像：不猜


# ---------- 表头 ----------

def test_single_column_table(tmp_path):
    book(tmp_path / "会社休日.xlsx", {"休日": [["日付"], [datetime(2026, 12, 29)], [datetime(2026, 12, 30)]]})
    table, _ = table_of("「会社休日.xlsx」を読み込む。", tmp_path)
    assert [n for n, _ in table.type.columns] == ["日付"] and len(table) == 2


def test_two_cell_title_is_an_error_not_a_guess(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["請求一覧", "2026年10月"], ["番号", "取引先", "金額"], ["A", "B", 1]]})
    assert codes("「a.xlsx」を読み込む。", tmp_path) == ["E3021"]


def test_blank_header_cell(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["番号", None, "金額"], ["A", "B", 1]]})
    assert codes("「a.xlsx」を読み込む。", tmp_path) == ["E3023"]


def test_empty_rows_and_trailing_columns(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["番号", "金額", None, None], ["A", 1], [], [None, None], ["B", 2, None]]})
    table, _ = table_of("「a.xlsx」を読み込む。", tmp_path)
    assert table.rows == [("A", 1), ("B", 2)]


def test_empty_sheet(tmp_path):
    book(tmp_path / "a.xlsx", {"s": []})
    assert codes("「a.xlsx」を読み込む。", tmp_path) == ["E3028"]


# ---------- 单元格 ----------

def test_cell_types(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [
        ["小数", "率", "日時", "時刻", "真偽", "文字の数", "負"],
        [0.1 + 0.2, 0.1, datetime(2026, 10, 31, 9, 30), time(9, 30), True, "1,000", -500],
        [1.5, 0.08, datetime(2026, 11, 1), time(18, 0), False, "2,000", 3],
    ]}, formats={("s", "B2"): "0%", ("s", "B3"): "0%"})
    table, _ = table_of("「a.xlsx」を読み込む。", tmp_path)
    first = table.rows[0]
    assert first[0] == Decimal("0.3")                        # Excel の表示と同じ精度
    assert first[1] == Decimal("0.1")                        # 10% は 0.1
    assert first[2] == "2026/10/31 09:30:00"                 # 時刻つき → 文字列（時刻を黙って捨てない）
    assert first[3] == "09:30:00"
    assert first[4] == "TRUE"
    assert first[5] == 1000                                  # 文字として入った数も CSV と同じ規則
    assert first[6] == -500


def test_very_small_and_large_numbers(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["a", "b"], [0.0000001, 1e20]]})
    table, _ = table_of("「a.xlsx」を読み込む。", tmp_path)
    assert table.rows[0] == (Decimal("0.0000001"), 100000000000000000000)


def test_merged_cells_are_blank(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["取引先", "金額"], ["山田商事", 1], [None, 2]]}, merge={"s": ["A2:A3"]})
    table, _ = table_of("「a.xlsx」を読み込む。", tmp_path)
    assert table.rows[1][0] is BLANK


def test_formula_with_saved_result(tmp_path):
    path = book(tmp_path / "a.xlsx", {"s": [["金額", "税"], [1000, "=A2*0.1"]]})
    cache_formula_results(path, {"B2": "100.00000000000001"})
    table, _ = table_of("「a.xlsx」を読み込む。", tmp_path)
    assert table.rows[0][1] == 100


def test_formula_without_saved_result(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["金額", "税"], [1000, "=A2*0.1"]]})
    assert codes("「a.xlsx」を読み込む。", tmp_path) == ["E3073"]


def test_invalid_date_reports_excel_row(tmp_path):
    book(tmp_path / "a.xlsx", {"s": [["表題"], ["番号", "期日"], ["A", "2026/2/30"]]})
    errors = compile_source(Source("「a.xlsx」を読み込む。"), base_dir=tmp_path).errors
    assert errors[0].code == "E3050" and "3行目" in errors[0].message


# ---------- 文件 ----------

def test_file_errors(tmp_path):
    (tmp_path / "古い.xls").write_bytes(b"dummy")
    (tmp_path / "壊れ.xlsx").write_text("これは Excel ではない", encoding="utf-8")
    (tmp_path / "明細.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert codes("「古い.xls」を読み込む。", tmp_path) == ["E3070"]
    assert codes("「壊れ.xlsx」を読み込む。", tmp_path) == ["E3074"]
    assert codes("「無い.xlsx」を読み込む。", tmp_path) == ["E3019"]
    assert codes("「明細.csv」の「1」シートを読み込む。", tmp_path) == ["E3075"]
    assert codes("「a.txt」を読み込む。", tmp_path) == ["E3027"]


def test_sheet_only_where_files_are_used(invoices):
    assert codes("「請求一覧.xlsx」の「10月」シートを表示する。", invoices) == ["E3076"]
    assert codes("名前は「請求一覧.xlsx」の「10月」シートとする。", invoices) == ["E3076"]


def test_writing_excel_is_not_yet_supported(invoices):
    code = "「請求一覧.xlsx」の「11月」シートを読み込んで、"
    assert codes(code + "「出力/a.xlsx」に書き出す。", invoices) == ["E3027"]
    assert codes(code + "「出力/a.csv」の「x」シートに書き出す。", invoices) == ["E3075"]


def test_xlsm_and_permissions(tmp_path):
    book(tmp_path / "マクロ.xlsm", {"s": [["a", "b"], [1, 2]]})
    code = "この処理が使うもの：\n  読み込み：「マクロ.xlsm」\nここまで。\n「マクロ.xlsm」を読み込んで、表示する。"
    output, errors = run_code(code, tmp_path)
    assert errors == [] and "1" in output[0]
    undeclared = "この処理が使うもの：\nここまで。\n「マクロ.xlsm」を読み込む。"
    assert codes(undeclared, tmp_path) == ["E3059"]


def test_execution_log_records_excel_read(invoices):
    import json
    from dandori.api import run_with_log
    code = "「請求一覧.xlsx」の「11月」シートを読み込んで、表示する。"
    outcome = run_with_log(Source(code, "p.dandori"), Context(output=lambda _: None, base_dir=invoices),
                           log_dir=invoices / "実行記録")
    record = json.loads(outcome.log_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["reads"][0]["file"] == "請求一覧.xlsx" and record["reads"][0]["rows"] == 1
