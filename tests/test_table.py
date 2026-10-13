"""阶段 3-1：表格与 CSV 读取"""

from decimal import Decimal
from pathlib import Path

import pytest

from dandori.api import compile_source, run
from dandori.connectors import ConnectorError
from dandori.connectors.csv import read_csv
from dandori.frontend import ast
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.frontend.tokens import TokenKind as K
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context, execute
from dandori.table.interface import BLANK
from dandori.types import Type

EXAMPLES = Path(__file__).parent.parent / "examples"

MEISAI = "取引先,金額,支払い期日\n山田商事,1000,2026-10-31\n佐藤物産,\"2,500\",\n"


def write(directory: Path, name: str, text: str, encoding: str = "utf-8") -> Path:
    path = directory / name
    path.write_bytes(text.encode(encoding))
    return path


def run_in(directory: Path, code: str):
    output = []
    errors = run(Source(code), Context(output=output.append, base_dir=directory))
    return output, [e.code for e in errors]


# ---------- CSV 连接器 ----------

@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp932"])
def test_encodings(tmp_path, encoding):
    table = read_csv(write(tmp_path, "a.csv", MEISAI, encoding), "a.csv")
    assert table.display_names == ["取引先", "金額", "支払い期日"]
    assert table.column("取引先") == ["山田商事", "佐藤物産"]


def test_undecodable_file(tmp_path):
    path = tmp_path / "a.csv"
    path.write_bytes(b"\xff\xfe\x00\x81")
    with pytest.raises(ConnectorError) as e:
        read_csv(path, "a.csv")
    assert e.value.code == "E3020"


def test_number_formats_and_types(tmp_path):
    text = "a,b,c,d\n1,\"1,000\",１２,3.50\n-2,\"12,345,678\",－３,3.0\n"
    table = read_csv(write(tmp_path, "a.csv", text), "a.csv")
    assert [t for _, t in table.type.columns] == [Type.NUMBER] * 4
    assert table.column("a") == [1, -2]
    assert table.column("b") == [1000, 12345678]
    assert table.column("c") == [12, -3]                 # 全角数字、全角负号
    assert table.column("d") == [Decimal("3.50"), 3]      # 3.0 → 3


@pytest.mark.parametrize("cells, expected", [
    (["00123", "00456"], ["00123", "00456"]),           # 取引先コード
    (["0600001", "1000005"], ["0600001", "1000005"]),   # 郵便番号：一个以 0 开头，整列都是文字列
    (["0312345678"], ["0312345678"]),                   # 電話番号
    (["０１２３"], ["０１２３"]),                       # 全角也一样，保留原文
])
def test_leading_zero_codes_are_strings(tmp_path, cells, expected):
    text = "コード\n" + "\n".join(cells) + "\n"
    table = read_csv(write(tmp_path, "a.csv", text), "a.csv")
    assert table.type.columns == (("コード", Type.STRING),)
    assert table.column("コード") == expected


@pytest.mark.parametrize("cells, expected", [
    (["0", "10"], [0, 10]),                             # 「0」本身是数
    (["0.5", "1"], [Decimal("0.5"), 1]),               # 小数
    (["-0.25"], [Decimal("-0.25")]),
])
def test_zero_and_decimals_are_still_numbers(tmp_path, cells, expected):
    text = "値\n" + "\n".join(cells) + "\n"
    table = read_csv(write(tmp_path, "a.csv", text), "a.csv")
    assert table.type.columns == (("値", Type.NUMBER),)
    assert table.column("値") == expected


def test_mixed_column_is_string(tmp_path):
    table = read_csv(write(tmp_path, "a.csv", "a\n1\nx\n"), "a.csv")
    assert table.type.columns == (("a", Type.STRING),)
    assert table.column("a") == ["1", "x"]


def test_blanks_and_blank_lines(tmp_path):
    text = "a,b\n1, \n\n2,x\n,,\n"
    table = read_csv(write(tmp_path, "a.csv", text), "a.csv")
    assert len(table) == 2                                 # 完全空白的行跳过
    assert table.column("a") == [1, 2]
    assert table.column("b") == [BLANK, "x"]
    assert table.origins == ["a.csv の 2行目", "a.csv の 4行目"]


def test_header_is_normalized(tmp_path):
    table = read_csv(write(tmp_path, "a.csv", "ＩＤ,名前\n1,a\n"), "a.csv")
    assert table.type.names == ["ID", "名前"]
    assert table.display_names == ["ＩＤ", "名前"]          # 显示用原文


@pytest.mark.parametrize("text, code", [
    ("a,b\n1\n", "E3021"),
    ("a,a\n1,2\n", "E3022"),
    ("a,,c\n1,2,3\n", "E3023"),
    ("", "E3028"),
    ("\n\n", "E3028"),
])
def test_csv_errors(tmp_path, text, code):
    with pytest.raises(ConnectorError) as e:
        read_csv(write(tmp_path, "a.csv", text), "a.csv")
    assert e.value.code == code


def test_missing_file(tmp_path):
    with pytest.raises(ConnectorError) as e:
        read_csv(tmp_path / "x.csv", "x.csv")
    assert e.value.code == "E3019"


# ---------- 「の」 ----------

@pytest.fixture(scope="module")
def lexicon():
    return Lexicon.from_registry(default_registry())


def test_no_is_a_particle(lexicon):
    tokens, errors = tokenize(Source("明細の金額"), lexicon)
    assert errors == []
    assert [(t.kind, t.text) for t in tokens[:3]] == [(K.NAME, "明細"), (K.PARTICLE, "の"), (K.NAME, "金額")]


def test_declared_term_with_no_wins(lexicon):
    tokens, errors = tokenize(Source("用語：今月の合計。今月の合計を表示する。"), lexicon)
    assert errors == []
    assert (tokens[0].kind, tokens[0].text) == (K.NAME, "今月の合計")


def test_column_ref_tree(tmp_path):
    write(tmp_path, "明細.csv", MEISAI)
    result = compile_source(Source("「明細.csv」を読み込んで、明細とする。明細の「支払い期日」を表示する。"),
                            base_dir=tmp_path)
    assert result.errors == []
    expr = result.tree.sentences[1].steps[0].args[0].value
    assert isinstance(expr, ast.ColumnRef)
    assert (expr.table.name, expr.column) == ("明細", "支払い期日")


# ---------- 编译时检查 ----------

def compile_codes(directory, code):
    return [e.code for e in compile_source(Source(code), base_dir=directory).errors]


READ = "「明細.csv」を読み込んで、明細とする。"


@pytest.mark.parametrize("code, expected", [
    (READ + "明細の金頭を表示する。", ["E3025"]),
    (READ + "明細の「支払期日」を表示する。", ["E3025"]),
    ("合計は1とする。合計の金額を表示する。", ["E3026"]),
    ("「無.csv」を読み込んで、無とする。", ["E3019"]),
    ("「明細.xlsx」を読み込む。", ["E3027"]),
    ("名前は「明細.csv」とする。名前を読み込む。", ["E3024"]),
    (READ + "明細の取引先を足す。", ["E3007"]),
    (READ + "明細を足す。", ["E3007"]),
    (READ + "明細のを表示する。", ["E2018"]),
    (READ + "明細は「明細.csv」とする。", ["E3009"]),
    (READ + "もし明細の金額が1ならば、ここまで。", ["E3013"]),
])
def test_compile_errors(tmp_path, code, expected):
    write(tmp_path, "明細.csv", MEISAI)
    assert compile_codes(tmp_path, code) == expected


def test_typo_hint(tmp_path):
    write(tmp_path, "明細.csv", MEISAI)
    result = compile_source(Source(READ + "明細の金頭を表示する。"), base_dir=tmp_path)
    assert result.errors[0].message == "「明細」に「金頭」という列はありません（「金額」のことですか？）"


def test_failed_read_does_not_cascade(tmp_path):
    # 文件不存在：只报一次，后面使用「明細」时不再连锁报错
    assert compile_codes(tmp_path, READ + "明細の金額を表示する。") == ["E3019"]


def test_different_tables_are_different_types(tmp_path):
    write(tmp_path, "a.csv", "x\n1\n")
    write(tmp_path, "b.csv", "y\n1\n")
    codes = compile_codes(tmp_path, "「a.csv」を読み込んで、表とする。「b.csv」を読み込んで、表とする。")
    assert codes == ["E3009"]


# ---------- 执行 ----------

def test_display_and_sum(tmp_path):
    write(tmp_path, "明細.csv", MEISAI)
    output, errors = run_in(tmp_path, READ + "明細の取引先を表示する。明細の「支払い期日」を表示する。")
    assert errors == []
    assert output == ["山田商事、佐藤物産", "2026年10月31日、（空欄）"]


def test_table_display_is_aligned(tmp_path):
    write(tmp_path, "a.csv", "名前,金額\nA,5\n山田商事,1000\n")
    output, errors = run_in(tmp_path, "「a.csv」を読み込んで、表示する。")
    assert errors == []
    assert output[0].split("\n") == [
        "名前      金額",
        "A            5",
        "山田商事  1000",
    ]


def test_blank_in_calculation_reports_row(tmp_path):
    write(tmp_path, "明細.csv", "金額,x\n1,a\n,b\n3,c\n")
    output = []
    errors = run(Source(READ + "明細の金額を足して表示する。"),
                 Context(output=output.append, base_dir=tmp_path))
    assert output == []
    assert errors[0].message.startswith("計算する値に空欄があります（明細.csv の 3行目）。")
    assert "空欄でないもの" in errors[0].message          # 提示处理方法


def test_file_changed_between_compile_and_run(tmp_path):
    path = write(tmp_path, "明細.csv", MEISAI)
    result = compile_source(Source(READ + "明細の金額を表示する。"), base_dir=tmp_path)
    assert result.errors == []
    path.write_text("取引先,数量\nA,1\n", encoding="utf-8")       # 编译后文件被改了
    from dandori.runtime.interpreter import ExecutionError
    with pytest.raises(ExecutionError) as e:
        execute(result.program, Context(output=lambda _: None, base_dir=tmp_path))
    assert e.value.diagnostic.code == "E5005"


def test_file_deleted_between_compile_and_run(tmp_path):
    path = write(tmp_path, "明細.csv", MEISAI)
    result = compile_source(Source(READ), base_dir=tmp_path)
    path.unlink()
    from dandori.runtime.interpreter import ExecutionError
    with pytest.raises(ExecutionError) as e:
        execute(result.program, Context(output=lambda _: None, base_dir=tmp_path))
    assert e.value.diagnostic.code == "E5005"
    assert "見つかりません" in e.value.diagnostic.message


def test_table_example_file():
    code = (EXAMPLES / "表.dandori").read_text(encoding="utf-8")
    output, errors = run_in(EXAMPLES, code)
    assert errors == []
    assert output[1:] == [
        "山田商事、佐藤物産、山田商事、ＡＢＣ株式会社、佐藤物産",
        "290300",
        "2026年10月31日、2026年10月31日、2026年11月30日、2026年11月30日、（空欄）",
    ]
