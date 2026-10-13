"""阶段 3-4：写出文件、效果系统、试运行"""

import pytest

from dandori.api import compile_source, run
from dandori.cli import main
from dandori.frontend.source import Source
from dandori.plugin.registry import Effect
from dandori.runtime.interpreter import Context

MEISAI = "取引先,金額,メモ\n山田商事,1000,\n佐藤物産,\"2,500\",急ぎ\n山田商事,500,\n"
READ = "「明細.csv」を読み込んで、明細とする。"


@pytest.fixture
def data(tmp_path):
    (tmp_path / "明細.csv").write_text(MEISAI, encoding="utf-8")
    return tmp_path


def run_in(directory, code, dry_run=False):
    output = []
    ctx = Context(output=output.append, base_dir=directory, dry_run=dry_run)
    errors = run(Source(READ + code), ctx)
    return output, [e.code for e in errors], ctx


def compile_codes(directory, code):
    return [e.code for e in compile_source(Source(READ + code), base_dir=directory).errors]


# ---------- 写出 ----------

def test_write_csv_format(data):
    _, errors, ctx = run_in(data, "明細の金額を取引先ごとに合計して、集計とする。集計を「集計.csv」に書き出す。")
    assert errors == []
    raw = (data / "集計.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")                           # UTF-8 带 BOM：Excel 不乱码
    assert raw.decode("utf-8-sig") == "取引先,金額\r\n山田商事,1500\r\n佐藤物産,2500\r\n"   # CRLF，无千位分隔符
    assert ctx.transaction.report(done=True) == ["「集計.csv」を新規作成しました（2行）"]


def test_blank_written_as_empty_and_roundtrip(data):
    _, errors, _ = run_in(data, "明細を「写し.csv」に書き出す。")
    assert errors == []
    text = (data / "写し.csv").read_bytes().decode("utf-8-sig")
    assert text.splitlines()[1] == "山田商事,1000,"
    # 写出的文件可以再读回来，内容相同
    output, errors, _ = run_in(data, "「写し.csv」を読み込んで、複写とする。複写の金額を合計して、表示する。")
    assert errors == [] and output == ["4000"]


def test_creates_subfolder(data):
    _, errors, _ = run_in(data, "明細を「出力/月次/写し.csv」に書き出す。")
    assert errors == []
    assert (data / "出力" / "月次" / "写し.csv").exists()


def test_overwrite(data):
    (data / "集計.csv").write_text("古い内容\n", encoding="utf-8")
    _, errors, ctx = run_in(data, "「集計.csv」を明細で上書きする。")
    assert errors == []
    assert (data / "集計.csv").read_bytes().decode("utf-8-sig").startswith("取引先,金額,メモ")
    assert ctx.transaction.report(done=True) == ["「集計.csv」を上書きしました（3行）"]


def test_overwrite_creates_when_missing(data):
    _, errors, ctx = run_in(data, "「新しい.csv」を明細で上書きする。")
    assert errors == []
    assert ctx.transaction.report(done=True) == ["「新しい.csv」を新規作成しました（3行）"]


# ---------- 事务 ----------

def test_nothing_written_when_error_occurs(data):
    _, errors, _ = run_in(data, "明細を「A.csv」に書き出す。明細を「B.csv」に書き出す。1を0で割って表示する。")
    assert errors == ["E5001"]
    assert not (data / "A.csv").exists()
    assert not (data / "B.csv").exists()


def test_no_temp_files_left(data):
    run_in(data, "明細を「A.csv」に書き出す。")
    assert sorted(p.name for p in data.iterdir()) == ["A.csv", "明細.csv"]


def test_file_created_after_compile(data):
    # 编译后、执行前被别人创建了：执行时报错，什么都不写
    result = compile_source(Source(READ + "明細を「A.csv」に書き出す。明細を「B.csv」に書き出す。"), base_dir=data)
    assert result.errors == []
    (data / "B.csv").write_text("別の人が作った\n", encoding="utf-8")
    from dandori.runtime.interpreter import ExecutionError, execute
    ctx = Context(output=lambda _: None, base_dir=data)
    with pytest.raises(ExecutionError) as e:
        execute(result.program, ctx)
    assert e.value.diagnostic.code == "E5008"
    assert not (data / "A.csv").exists()
    assert (data / "B.csv").read_text(encoding="utf-8") == "別の人が作った\n"


# ---------- 试运行 ----------

def test_dry_run_writes_nothing(data):
    (data / "既存.csv").write_text("x\n", encoding="utf-8")
    output, errors, ctx = run_in(data, "明細を数えて、表示する。明細を「新.csv」に書き出す。「既存.csv」を明細で上書きする。",
                                 dry_run=True)
    assert errors == []
    assert output == ["3"]                                          # 计算、显示照常执行
    assert not (data / "新.csv").exists()
    assert (data / "既存.csv").read_text(encoding="utf-8") == "x\n"
    assert ctx.transaction.report(done=False) == [
        "「新.csv」を新規作成します（3行）",
        "「既存.csv」を上書きします（3行）",
    ]


# ---------- 説明 ----------

def test_resources(data):
    result = compile_source(Source(READ + """
        明細を「A.csv」に書き出す。
        もし1が1ならば、
          「B.csv」を明細で上書きする。
        ここまで。
    """), base_dir=data)
    assert result.errors == []
    assert [(r.effect, r.path, r.explain, r.conditional) for r in result.resources] == [
        (Effect.READ, "明細.csv", "「明細.csv」を読み込みます", False),
        (Effect.WRITE, "A.csv", "「A.csv」を新規作成します", False),
        (Effect.WRITE, "B.csv", "「B.csv」を上書きします", True),
    ]


def test_cli_explain_does_not_run(data, capsys):
    program = data / "p.dandori"
    program.write_text(READ + "明細を「A.csv」に書き出す。1を0で割って表示する。", encoding="utf-8")
    assert main(["説明", str(program)]) == 0
    out = capsys.readouterr().out
    assert "「明細.csv」を読み込みます" in out
    assert "「A.csv」を新規作成します" in out
    assert not (data / "A.csv").exists()


def test_cli_dry_run_and_run(data, capsys):
    program = data / "p.dandori"
    program.write_text(READ + "明細を「A.csv」に書き出す。", encoding="utf-8")
    assert main(["試し", str(program)]) == 0
    assert "（試し実行：ファイルは書き出していません）" in capsys.readouterr().err
    assert not (data / "A.csv").exists()
    assert main(["実行", str(program)]) == 0
    assert "「A.csv」を新規作成しました（3行）" in capsys.readouterr().err
    assert (data / "A.csv").exists()


# ---------- 编译时检查 ----------

@pytest.mark.parametrize("code, expected", [
    ("明細を「../外.csv」に書き出す。", ["E3038"]),
    ("明細を「/tmp/外.csv」に書き出す。", ["E3038"]),
    ("明細を「C:\\\\外.csv」に書き出す。", ["E3038"]),
    ("「../明細.csv」を読み込む。", ["E3038"]),
    ("明細を「明細.csv」に書き出す。", ["E3039"]),
    ("明細を「A.csv」に書き出す。「A.csv」を明細で上書きする。", ["E3040"]),
    ("明細を「A.csv」に書き出す。「A.csv」を読み込む。", ["E3041"]),
    ("「明細.csv」を明細で上書きする。「明細.csv」を読み込む。", ["E3041"]),
    ("3回繰り返して、明細を「A.csv」に書き出す。ここまで。", ["E3042"]),
    ("明細のそれぞれについて、もし金額が1ならば、「A.csv」を明細で上書きする。ここまで。ここまで。", ["E3042"]),
    ("1を「A.csv」に書き出す。", ["E3007"]),
    ("明細を「A.xlsx」に書き出す。", ["E3027"]),
    ("名前は「A.csv」とする。明細を名前に書き出す。", ["E3024"]),
])
def test_compile_errors(data, code, expected):
    assert compile_codes(data, code) == expected


def test_read_then_overwrite_same_file_is_allowed(data):
    # 读取后覆盖同一个文件（更新文件）：编译时读到的就是写之前的内容，没有歧义
    _, errors, _ = run_in(data, "明細から金額が1000以上のものを絞り込んで、対象とする。「明細.csv」を対象で上書きする。")
    assert errors == []
    assert (data / "明細.csv").read_bytes().decode("utf-8-sig").count("\r\n") == 3   # 見出し + 2 行


@pytest.mark.parametrize("name", ["/tmp/外.csv", "\\tmp\\外.csv", "C:外.csv", "C:\\外.csv", "..\\外.csv", "a/../../外.csv"])
def test_outside_folder_on_every_os(name):
    # Windows 上「/tmp/外.csv」不算绝对路径，但与文件夹连接后会跑到 C:\tmp：按 POSIX 的规则判断
    from dandori.stdlib.files import outside_folder
    assert outside_folder(name) is None


def test_inside_folder():
    from dandori.stdlib.files import outside_folder
    assert outside_folder("出力\\a.csv").as_posix() == "出力/a.csv"
