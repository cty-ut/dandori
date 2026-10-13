"""阶段 6：权限声明、执行日志、公司日历"""

import dataclasses
import json
from datetime import date

import pytest

from dandori.api import compile_source, run, run_with_log
from dandori.cli import main
from dandori.frontend.source import Source
from dandori.runtime.interpreter import Context, ExecutionError, execute

CSV = "取引先,金額\nA,100\nB,200\n"


@pytest.fixture
def data(tmp_path):
    (tmp_path / "明細.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


def compile_codes(directory, code):
    return [e.code for e in compile_source(Source(code), base_dir=directory).errors]


PERMS = """この処理が使うもの：
  読み込み：「明細.csv」
  書き出し：「出力」フォルダ
ここまで。
「明細.csv」を読み込んで、明細とする。
"""


# ---------- 6-1 权限声明 ----------

def test_within_permissions(data):
    assert compile_codes(data, PERMS + "明細を「出力/a.csv」に書き出す。明細を「出力/月次/b.csv」に書き出す。") == []


@pytest.mark.parametrize("extra, expected", [
    ("明細を「a.csv」に書き出す。", ["E3059"]),                   # 文件夹之外
    ("「出力/a.csv」を明細で上書きする。", ["E3059"]),             # 声明的是「書き出し」，不是「上書き」
    ("「出力外/a.csv」を読み込む。", ["E3059"]),                   # 「出力外」不在「出力」文件夹里
])
def test_outside_permissions(data, extra, expected):
    (data / "出力外").mkdir()
    (data / "出力外" / "a.csv").write_text("x\n1\n", encoding="utf-8")
    assert compile_codes(data, PERMS + extra) == expected


def test_unapproved_modification_is_caught(data):
    # 审核后有人加了一行覆盖「売上台帳.csv」：编译时报错，指出那一行
    (data / "売上台帳.csv").write_text("x\n1\n", encoding="utf-8")
    result = compile_source(Source(PERMS + "「売上台帳.csv」を明細で上書きする。"), base_dir=data)
    assert [e.code for e in result.errors] == ["E3059"]
    assert result.errors[0].message == "「売上台帳.csv」の上書きは「この処理が使うもの」に書かれていません"


def test_setting_files_need_permission(data):
    (data / "休日.csv").write_text("日付\n2026/12/29\n", encoding="utf-8")
    assert compile_codes(data, PERMS + "暦の会社休日は「休日.csv」とする。") == ["E3059"]


def test_overwrite_permission(data):
    code = """この処理が使うもの：
      読み込み：「明細.csv」
      上書き：「明細.csv」
    ここまで。
    「明細.csv」を読み込んで、明細とする。「明細.csv」を明細で上書きする。"""
    assert compile_codes(data, code) == []


@pytest.mark.parametrize("code, expected", [
    ("1を表示する。" + PERMS, ["E3057"]),
    (PERMS + PERMS.split("「明細.csv」を読み込んで")[0], ["E3058"]),
    ("この処理が使うもの：\n書き出し：「../外」フォルダ\nここまで。", ["E3038"]),
    ("この処理が使うもの：\n読み込み：「a.csv」\n", ["E2016"]),
])
def test_declaration_errors(data, code, expected):
    assert compile_codes(data, code) == expected


def test_runtime_double_check(data):
    # 编译时通过的程序，执行时权限被改小（模拟被篡改）：连接器拒绝访问
    result = compile_source(Source(PERMS + "明細を「出力/a.csv」に書き出す。"), base_dir=data)
    assert result.errors == []
    tampered = dataclasses.replace(result.program, permissions=(("読み込み", "明細.csv", False),))
    with pytest.raises(ExecutionError) as e:
        execute(tampered, Context(output=lambda _: None, base_dir=data))
    assert e.value.diagnostic.code == "E5015"


def test_without_declaration_is_unrestricted(data):
    assert compile_codes(data, "「明細.csv」を読み込んで、明細とする。明細を「a.csv」に書き出す。") == []


def test_unused_permissions(data):
    result = compile_source(Source(PERMS), base_dir=data)
    assert result.unused_permissions() == [("書き出し", "出力", True)]


def test_explain_shows_declaration(data, capsys):
    program = data / "p.dandori"
    program.write_text(PERMS, encoding="utf-8")
    assert main(["説明", str(program)]) == 0
    out = capsys.readouterr().out
    assert "この処理が使うもの（宣言）：" in out
    assert "※ 書き出し：「出力」フォルダ は宣言されていますが、使われていません" in out


def test_explain_warns_without_declaration(data, capsys):
    program = data / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    main(["説明", str(program)])
    assert "「この処理が使うもの」が書かれていません" in capsys.readouterr().out


# ---------- 6-2 执行日志 ----------

def logged_run(data, code, today=date(2026, 11, 2), dry_run=False):
    source = Source(code, str(data / "月次.dandori"))
    ctx = Context(output=lambda _: None, base_dir=data, today=today, dry_run=dry_run)
    return run_with_log(source, ctx, log_dir=data / "実行記録")


def test_log_content(data):
    outcome = logged_run(data, PERMS + "明細を「出力/a.csv」に書き出す。")
    assert outcome.errors == []
    record = json.loads(outcome.log_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["today"] == "2026-11-02"
    assert record["program"]["name"] == "月次.dandori"
    assert len(record["program"]["sha256"]) == 64
    assert [(r["file"], r["rows"]) for r in record["reads"]] == [("明細.csv", 2)]
    assert record["writes"] == [{"file": "出力/a.csv", "rows": 2, "action": "新規作成"}]
    assert record["result"]["status"] == "ok"
    text = outcome.log_path.read_text(encoding="utf-8")
    assert "結果：正常終了" in text
    assert "「明細.csv」 2行" in text


def test_log_does_not_contain_data(data):
    outcome = logged_run(data, "「明細.csv」を読み込んで、明細とする。明細を表示する。")
    for path in (outcome.log_path, outcome.log_path.with_suffix(".json")):
        content = path.read_text(encoding="utf-8")
        assert "取引先" not in content and "100" not in content.replace("100%", "")


def test_log_fingerprint_changes_with_input(data):
    first = logged_run(data, "「明細.csv」を読み込む。")
    (data / "明細.csv").write_text(CSV + "C,300\n", encoding="utf-8")
    second = logged_run(data, "「明細.csv」を読み込む。")
    sha = [json.loads(o.log_path.with_suffix(".json").read_text(encoding="utf-8"))["reads"][0]["sha256"]
           for o in (first, second)]
    assert sha[0] != sha[1]


def test_logs_are_never_overwritten(data):
    paths = {logged_run(data, "1を表示する。").log_path for _ in range(3)}
    assert len(paths) == 3
    assert len(list((data / "実行記録").glob("*.txt"))) == 3


def test_log_on_runtime_error(data):
    outcome = logged_run(data, "明細は1とする。明細を0で割って表示する。")
    assert [e.code for e in outcome.errors] == ["E5001"]
    text = outcome.log_path.read_text(encoding="utf-8")
    assert "途中でエラー" in text and "ファイルは書き出していません" in text
    assert "[E5001] 1行目" in text


def test_log_on_compile_error(data):
    outcome = logged_run(data, "それを表示する。")
    record = json.loads(outcome.log_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert record["result"]["executed"] is False
    assert record["result"]["errors"][0]["code"] == "E3006"


def test_dry_run_writes_no_log(data):
    outcome = logged_run(data, "1を表示する。", dry_run=True)
    assert outcome.log_path is None
    assert not (data / "実行記録").exists()


def test_cli_logs_only_for_program_files(data, capsys):
    program = data / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    assert main(["実行", str(program)]) == 0
    assert "実行記録：実行記録/" in capsys.readouterr().err
    main(["試し", str(program)])
    main(["実行", "-c", "1を表示する。"])
    assert len(list((data / "実行記録").glob("*.txt"))) == 1


# ---------- 6-3 公司日历 ----------

COMPANY = "日付,理由\n2026/12/29,年末年始\n2026/12/30,年末年始\n2026/12/31,年末年始\n2027/01/04,年末年始\n2026/08/13,夏季休業\n"


@pytest.fixture
def company(tmp_path):
    (tmp_path / "会社休日.csv").write_text(COMPANY, encoding="utf-8")
    return tmp_path


def run_company(directory, code):
    output = []
    errors = run(Source("暦の会社休日は「会社休日.csv」とする。" + code),
                 Context(output=output.append, base_dir=directory, today=date(2026, 10, 14)))
    return output, [e.code for e in errors]


@pytest.mark.parametrize("expr, expected", [
    ("2026年12月28日+1会社営業日", "2027年1月5日"),     # 公司 12/29～1/4 休息
    ("2026年12月28日+1営業日", "2026年12月29日"),       # 银行依然营业
    ("2026年12月25日+2会社営業日", "2027年1月5日"),
    ("2026年8月12日+1会社営業日", "2026年8月14日"),
    ("2027年1月5日-1会社営業日", "2026年12月28日"),
])
def test_company_business_days(company, expr, expected):
    output, errors = run_company(company, f"{expr}を表示する。")
    assert errors == [] and output == [expected]


@pytest.mark.parametrize("cond, expected", [
    ("2026年8月13日が会社休日", True),
    ("2026年8月13日が営業日", True),                  # 银行营业
    ("2026年8月14日が会社営業日", True),
    ("2027年1月2日が会社休日", True),                 # 周末
    ("2026年11月3日が会社休日", True),                # 祝日
    ("2027年1月4日が会社休日でない", False),
])
def test_company_conditions(company, cond, expected):
    output, errors = run_company(company, f"もし{cond}ならば、「はい」を表示する。そうでなければ、「いいえ」を表示する。ここまで。")
    assert errors == [] and output == ["はい" if expected else "いいえ"]


def test_company_calendar_requires_setting(tmp_path):
    assert compile_codes(tmp_path, "今日+3会社営業日を表示する。") == ["E3060"]
    assert compile_codes(tmp_path, "もし今日が会社休日ならば、ここまで。") == ["E3060"]


def test_company_file_first_column_must_be_dates(tmp_path):
    (tmp_path / "会社休日.csv").write_text("理由,日付\n年末,2026/12/29\n", encoding="utf-8")
    assert compile_codes(tmp_path, "暦の会社休日は「会社休日.csv」とする。") == ["E3061"]


def test_company_file_is_logged_as_read(company):
    source = Source("暦の会社休日は「会社休日.csv」とする。今日+1会社営業日を表示する。", str(company / "p.dandori"))
    outcome = run_with_log(source, Context(output=lambda _: None, base_dir=company),
                           log_dir=company / "実行記録")
    record = json.loads(outcome.log_path.with_suffix(".json").read_text(encoding="utf-8"))
    assert [r["file"] for r in record["reads"]] == ["会社休日.csv"]
