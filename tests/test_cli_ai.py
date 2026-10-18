"""dandori_ai.cli のテスト：表示の分岐・エラーメッセージ・終了コード（ネットワークは使わない）"""

import pytest

from dandori_ai import cli
from dandori_ai.cli import main
from dandori_ai.loop import GenerationFailed
from dandori_ai.providers.base import ProviderError
from dandori_ai.providers.fake import FakeProvider, submit

GOOD = "この処理が使うもの：\n  読み込み：「明細.csv」\nここまで。\n「明細.csv」を読み込んで、明細とする。\n明細の金額を合計して表示する。\n"
WITH_WRITE = (GOOD.replace("ここまで。", "  書き出し：「出力」フォルダ\nここまで。")
              + "明細を「出力/a.csv」に書き出す。\n")


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "明細.csv").write_text("取引先,金額\n山田商事,1000\n佐藤物産,2000\n", encoding="utf-8")
    return tmp_path


def never_asked(prompt):
    raise AssertionError(f"質問されるはずがない：{prompt}")


def test_make_writes_file_and_prints_next_steps(folder, capsys):
    output = folder / "合計.dandori"
    provider = FakeProvider([submit(GOOD, "合計を表示します")])
    assert main(["作る", "合計して", "--出力", str(output)], provider_factory=lambda: provider, ask=never_asked) == 0
    captured = capsys.readouterr()
    assert output.read_text(encoding="utf-8") == GOOD
    assert "【概要】合計を表示します" in captured.out
    assert "【確認事項】" not in captured.out
    assert "【使うルールデータ】" not in captured.out
    assert f"dandori 説明 {output}" in captured.out and f"dandori 試し {output}" in captured.out
    assert "AI がプログラムを書いています" in captured.err


def test_make_refuses_to_overwrite_with_message(folder, capsys):
    output = folder / "a.dandori"
    output.write_text("既存", encoding="utf-8")
    assert main(["作る", "x", "--出力", str(output)], provider_factory=lambda: FakeProvider([])) == 1
    assert "はすでにあります（上書きしません）" in capsys.readouterr().err


def test_report_lists_rule_data_and_no_file_access(folder, capsys, monkeypatch):
    monkeypatch.setattr(cli, "explain", lambda result: {"files": [], "rule_data": ["会社休日.csv"]})
    output = folder / "a.dandori"
    assert main(["作る", "x", "--出力", str(output)], provider_factory=lambda: FakeProvider([submit(GOOD)])) == 0
    out = capsys.readouterr().out
    assert "ファイルは読み書きしません" in out
    assert "【使うルールデータ】\n  会社休日.csv" in out


def test_generation_failure_prints_last_errors(folder, capsys, monkeypatch):
    def failing(provider, toolbox, request):
        raise GenerationFailed("AI が最後まで書けませんでした", ["E3006", "E3025"])
    monkeypatch.setattr(cli, "run_session", failing)
    output = folder / "a.dandori"
    assert main(["作る", "x", "--出力", str(output)], provider_factory=lambda: FakeProvider([])) == 1
    err = capsys.readouterr().err
    assert "エラー：AI が最後まで書けませんでした" in err
    assert "最後のエラー：E3006、E3025" in err
    assert not output.exists()


def test_generation_failure_without_last_errors(folder, capsys, monkeypatch):
    def failing(provider, toolbox, request):
        raise GenerationFailed("だめでした")
    monkeypatch.setattr(cli, "run_session", failing)
    assert main(["作る", "x", "--出力", str(folder / "a.dandori")], provider_factory=lambda: FakeProvider([])) == 1
    err = capsys.readouterr().err
    assert "エラー：だめでした" in err and "最後のエラー" not in err


def test_change_without_differences_and_yes_flag(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(GOOD, encoding="utf-8")
    provider = FakeProvider([submit(GOOD)])
    assert main(["変える", str(program), "そのまま", "--はい"], provider_factory=lambda: provider, ask=never_asked) == 0
    out = capsys.readouterr().out
    assert "（変更なし）" in out
    assert "改めて承認が必要です" not in out
    assert "そのまま" in provider.requests[0][0]["text"]


def test_change_asks_and_writes_on_yes(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(GOOD, encoding="utf-8")
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return " はい "
    assert main(["変える", str(program), "書き出して"], provider_factory=lambda: FakeProvider([submit(WITH_WRITE)]),
                ask=ask) == 0
    assert program.read_text(encoding="utf-8") == WITH_WRITE
    assert any("に書き込みますか？" in p for p in prompts)
    out = capsys.readouterr().out
    assert "＋ 書き出し：「出力」フォルダ" in out
    assert "に書きました" in out


def test_change_declined_does_not_write(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(GOOD, encoding="utf-8")
    assert main(["変える", str(program), "書き出して"], provider_factory=lambda: FakeProvider([submit(WITH_WRITE)]),
                ask=lambda prompt: "いいえ") == 0
    assert program.read_text(encoding="utf-8") == GOOD
    assert "書き込みませんでした。" in capsys.readouterr().out


def test_change_reports_removed_permission(folder, capsys):
    program = folder / "p.dandori"
    program.write_text(WITH_WRITE, encoding="utf-8")
    assert main(["変える", str(program), "書き出しをやめて", "--はい"],
                provider_factory=lambda: FakeProvider([submit(GOOD)]), ask=never_asked) == 0
    out = capsys.readouterr().out
    assert "改めて承認が必要です" in out
    assert "－ 書き出し：「出力」フォルダ" in out
    assert "＋" not in out.split("改めて承認が必要です", 1)[1].split("【概要】", 1)[0]


def test_default_provider_comes_from_environment_and_options(folder, monkeypatch):
    created = []

    def fake_make_provider(name, model):
        created.append((name, model))
        return FakeProvider([submit(GOOD)])
    monkeypatch.setattr(cli, "make_provider", fake_make_provider)
    monkeypatch.setenv("DANDORI_AI_PROVIDER", "deepseek")
    assert main(["作る", "x", "--出力", str(folder / "a.dandori")]) == 0
    assert main(["作る", "x", "--出力", str(folder / "b.dandori"), "--提供元", "openai", "--モデル", "m1"]) == 0
    assert created == [("deepseek", None), ("openai", "m1")]


def test_provider_creation_failure_is_reported(folder, capsys, monkeypatch):
    def broken(name, model):
        raise ProviderError("提供元「gemini」は使えません")
    monkeypatch.setattr(cli, "make_provider", broken)
    assert main(["作る", "x", "--出力", str(folder / "a.dandori"), "--提供元", "gemini"]) == 1
    assert "エラー：提供元「gemini」は使えません" in capsys.readouterr().err


def test_evaluate_without_cases_is_an_error(tmp_path, capsys):
    assert main(["評価", "--ケース", str(tmp_path)], provider_factory=lambda: FakeProvider([])) == 1
    assert f"「{tmp_path}」に評価のケースがありません" in capsys.readouterr().err


def test_evaluate_filters_cases_and_prints_report(tmp_path, capsys, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.evals, "load_cases", lambda folder: [{"name": "月次集計"}, {"name": "入金消込"}])

    def run_all(cases, provider, results_dir, progress=None):
        seen["names"] = [c["name"] for c in cases]
        seen["results_dir"] = results_dir
        for case in cases:
            progress(case["name"])
        return ["R"], results_dir / "保存先"
    monkeypatch.setattr(cli.evals, "run_all", run_all)
    monkeypatch.setattr(cli.evals, "report", lambda results: f"REPORT{results}")
    results = tmp_path / "結果"
    assert main(["評価", "--ケース", str(tmp_path), "--結果", str(results), "--だけ", "消込"],
                provider_factory=lambda: FakeProvider([])) == 0
    captured = capsys.readouterr()
    assert seen["names"] == ["入金消込"] and seen["results_dir"] == results
    assert "REPORT['R']" in captured.out
    assert f"生成したプログラムと結果：{results / '保存先'}" in captured.out
    assert "評価中：入金消込…" in captured.err


def test_evaluate_filter_matching_nothing_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.evals, "load_cases", lambda folder: [{"name": "月次集計"}])
    assert main(["評価", "--ケース", str(tmp_path), "--だけ", "存在しない"],
                provider_factory=lambda: FakeProvider([])) == 1
    assert "評価のケースがありません" in capsys.readouterr().err
