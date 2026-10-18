"""dandori.cli のテスト：表示の分岐・エラーメッセージ・終了コード"""

import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from dandori import api, cli
from dandori.cli import main
from dandori.dates import JST


def test_tokens_command_prints_position_and_token(capsys):
    assert main(["字句", "-c", "1を表示する。"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines and all(re.match(r"\s*\d+:\d+\s+\S", line) for line in lines)
    assert lines[0].lstrip().startswith("1:1")


def test_syntax_tree_command_prints_tree(capsys):
    assert main(["構文", "-c", "1を表示する。"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() and captured.err == ""


def test_syntax_tree_command_reports_errors(capsys):
    assert main(["構文", "-c", "それを表示する。"]) == 1
    assert "E3006" in capsys.readouterr().err


def test_run_reports_compile_error_with_exit_code_1(capsys):
    assert main(["実行", "-c", "それを表示する。"]) == 1
    captured = capsys.readouterr()
    assert "E3006" in captured.err
    assert captured.out == ""


def test_run_reports_runtime_error(capsys):
    assert main(["実行", "-c", "明細は1とする。明細を0で割って表示する。"]) == 1
    assert "E5001" in capsys.readouterr().err


def test_run_code_prints_output(capsys):
    assert main(["実行", "-c", "100と200を足して表示する。"]) == 0
    assert capsys.readouterr().out.strip() == "300"


def test_missing_file_and_code_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["実行"])
    assert exc.value.code == 2
    assert "ファイルか -c を指定してください" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        main(["なにか", "-c", "1を表示する。"])
    assert exc.value.code == 2


def test_invalid_today_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["実行", "-c", "今日を表示する。", "--今日", "あしたのあさって"])
    assert exc.value.code == 2
    assert "「あしたのあさって」は日付として読めません" in capsys.readouterr().err


def test_today_option_with_slashes(capsys):
    assert main(["実行", "-c", "今日を表示する。", "--今日", "2026/10/31"]) == 0
    assert capsys.readouterr().out.strip() == "2026年10月31日"


def test_dry_run_says_nothing_was_written(capsys, tmp_path):
    program = tmp_path / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    assert main(["試し", str(program)]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == "1"
    assert "（試し実行：ファイルは書き出していません）" in captured.err
    assert not (tmp_path / "実行記録").exists()


def test_run_file_writes_log_and_reports_relative_path(capsys, tmp_path):
    program = tmp_path / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    assert main(["実行", str(program)]) == 0
    assert "実行記録：実行記録/" in capsys.readouterr().err
    assert list((tmp_path / "実行記録").glob("*.txt"))


def test_run_warns_when_log_cannot_be_written(monkeypatch, capsys, tmp_path):
    program = tmp_path / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    monkeypatch.setattr(cli.api, "run_with_log", lambda source, ctx, log_dir: SimpleNamespace(errors=[], log_path=None))
    assert main(["実行", str(program)]) == 0
    assert "※ 実行記録を書き出せませんでした" in capsys.readouterr().err


def test_explain_without_files_or_declaration(capsys, tmp_path):
    program = tmp_path / "p.dandori"
    program.write_text("1を表示する。", encoding="utf-8")
    assert main(["説明", str(program)]) == 0
    out = capsys.readouterr().out
    assert "このプログラムはファイルを読み込んだり書き出したりしません" in out
    assert "「この処理が使うもの」が書かれていません" in out


def test_explain_reports_errors_and_does_not_explain(capsys):
    assert main(["説明", "-c", "それを表示する。"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "E3006" in captured.err


class StubResult:
    def __init__(self, **overrides):
        self.resources = [SimpleNamespace(explain="「出力/a.csv」を書き出します", conditional=True),
                          SimpleNamespace(explain="「明細.csv」を読み込みます", conditional=False)]
        self.permissions = [("読み込み", "明細.csv", False)]
        self.schedule = SimpleNamespace(describe=lambda: "毎月25日 9時")
        self.rule_data = ["会社休日.csv"]
        self.__dict__.update(overrides)

    def unused_permissions(self):
        return [("書き出し", "出力", True)]


def test_explain_full_output(monkeypatch, capsys, tmp_path):
    following = datetime(2026, 10, 25, 9, 0, tzinfo=JST)
    monkeypatch.setattr(cli.api, "next_run", lambda result, now, base_dir: following)
    cli._explain(StubResult(), tmp_path)
    out = capsys.readouterr().out
    assert "このプログラムは次のことをします：" in out
    assert "  「出力/a.csv」を書き出します（条件によって）" in out
    assert "  「明細.csv」を読み込みます\n" in out
    assert "  読み込み：「明細.csv」" in out
    assert "※ 書き出し：「出力」フォルダ は宣言されていますが、使われていません" in out
    assert "実行の予定：毎月25日 9時" in out
    assert "次の実行：2026年10月25日" in out and "09:00" in out
    assert "段取りの基盤（dandori-platform）" in out
    assert "使うルールデータ：\n  会社休日.csv" in out


def test_explain_schedule_that_cannot_be_computed(monkeypatch, capsys, tmp_path):
    def broken(result, now, base_dir):
        raise api.ScheduleError("休日の表がありません")
    monkeypatch.setattr(cli.api, "next_run", broken)
    cli._explain(StubResult(resources=[], schedule=SimpleNamespace(describe=lambda: "毎日"), rule_data=[]), tmp_path)
    out = capsys.readouterr().out
    assert "次の実行：計算できません（休日の表がありません）" in out
    assert "このプログラムはファイルを読み込んだり書き出したりしません" in out
    assert "使うルールデータ" not in out
