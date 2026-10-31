"""dandori_platform.cli のテスト：表示の分岐・エラーメッセージ・終了コード

基盤本体は別のテスト（test_platform.py）で確かめているので、ここでは基盤を差し替えて
コマンドラインの表示と終了コードだけを確かめる。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from dandori_platform import cli
from dandori_platform.platform import PlatformError


def make_review(**overrides):
    review = SimpleNamespace(
        program=SimpleNamespace(name="集計", path=Path("集計/集計.dandori")),
        diff=None, rule_diffs={}, permissions_added=[], permissions_removed=[],
        approvable=True, already_approved=False, errors=[],
        files=["「明細.csv」を読み込みます"], permissions=["読み込み：「明細.csv」"],
        schedule="毎月25日 9時", next_run="2026年10月23日（金）09:00", rule_data=[],
    )
    for key, value in overrides.items():
        setattr(review, key, value)
    return review


def make_run(**overrides):
    run = SimpleNamespace(slot=None, started_at="2026-10-14T10:05:33+09:00", program="集計",
                          status="正常終了", late=False, missed=0, error=None, log_path=None)
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


def make_status(**overrides):
    status = SimpleNamespace(program=SimpleNamespace(name="集計"), state="承認済み", changes=[], approval=None,
                             schedule="手動（予定なし）", next_run=None, last=None)
    for key, value in overrides.items():
        setattr(status, key, value)
    return status


class StubPlatform:
    """cli.Platform の代わり。呼び出しを記録し、決めた値を返す"""
    instances = []
    review_value = None
    register_error = None
    run_value = None
    statuses_value = ()
    history_value = ()
    interrupted_value = 0
    tick_values = ()

    def __init__(self, workspace, **kwargs):
        self.workspace = Path(workspace)
        self.kwargs = kwargs
        self.closed = False
        self.calls = []
        type(self).instances.append(self)

    def register(self, path, name):
        self.calls.append(("register", path, name))
        if self.register_error:
            raise self.register_error
        return SimpleNamespace(name=name or path.stem)

    def review(self, name):
        self.calls.append(("review", name))
        return self.review_value

    def approve(self, review, approver):
        self.calls.append(("approve", review, approver))

    def run(self, name):
        self.calls.append(("run", name))
        return self.run_value

    def statuses(self):
        return list(self.statuses_value)

    def history(self, name):
        self.calls.append(("history", name))
        return list(self.history_value)

    def interrupt_running(self):
        return self.interrupted_value

    def tick(self):
        self.calls.append(("tick",))
        return list(self.tick_values)

    def close(self):
        self.closed = True


@pytest.fixture
def stub(monkeypatch):
    class Fresh(StubPlatform):
        instances = []
    monkeypatch.setattr(cli, "Platform", Fresh)
    return Fresh


def never_asked(prompt):
    raise AssertionError(f"質問されるはずがない：{prompt}")


# ---------- 登録・エラー ----------

def test_register_with_custom_name_and_workspace(stub, tmp_path, capsys):
    assert cli.main(["登録", "a/b.dandori", "--名前", "月次", "--場所", str(tmp_path)]) == 0
    platform = stub.instances[0]
    assert platform.workspace == tmp_path and platform.closed
    assert platform.calls == [("register", Path("a/b.dandori"), "月次")]
    out = capsys.readouterr().out
    assert "「月次」を登録しました。動かす前に承認が必要です：" in out
    assert "dandori-platform 承認 月次 --承認者 〈名前〉" in out


def test_clock_is_passed_to_platform_only_when_given(stub, tmp_path):
    def clock():
        return None
    cli.main(["一覧", "--場所", str(tmp_path)], clock=clock)
    cli.main(["一覧", "--場所", str(tmp_path)])
    assert stub.instances[0].kwargs == {"clock": clock}
    assert stub.instances[1].kwargs == {}


def test_platform_error_gives_exit_code_1_and_closes(stub, tmp_path, capsys):
    stub.register_error = PlatformError("ファイルがありません")
    assert cli.main(["登録", "x.dandori", "--場所", str(tmp_path)]) == 1
    assert "エラー：ファイルがありません" in capsys.readouterr().err
    assert stub.instances[0].closed


def test_approver_is_required(stub, tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["承認", "集計", "--場所", str(tmp_path)])
    assert exc.value.code == 2
    assert "--承認者" in capsys.readouterr().err


# ---------- 承認 ----------

def test_approve_shows_all_review_sections(stub, tmp_path, capsys):
    stub.review_value = make_review(
        diff="-毎月25日\n+毎月20日",
        rule_diffs={"会社休日.csv": "+2026/11/04"},
        permissions_added=["書き出し：「出力」フォルダ"], permissions_removed=["読み込み：「古い.csv」"],
        rule_data=["会社休日.csv"])
    assert cli.main(["承認", "集計", "--承認者", "田中", "--はい", "--場所", str(tmp_path)], ask=never_asked) == 0
    out = capsys.readouterr().out
    assert "【集計】" in out
    assert "前回の承認からの変更：\n-毎月25日\n+毎月20日" in out
    assert "⚠ 規則のファイル「会社休日.csv」が変わりました：\n+2026/11/04" in out
    assert "  ＋ 書き出し：「出力」フォルダ" in out and "  － 読み込み：「古い.csv」" in out
    assert "  「明細.csv」を読み込みます" in out
    assert "実行の予定：毎月25日 9時" in out and "  次の実行：2026年10月23日（金）09:00" in out
    assert "使うルールデータ：\n  会社休日.csv" in out
    assert "田中 さんの承認を記録しました。" in out
    assert ("approve", stub.review_value, "田中") in stub.instances[0].calls


def test_approve_defaults_for_empty_sections(stub, tmp_path, capsys):
    stub.review_value = make_review(diff="", files=[], permissions=[], schedule=None, next_run=None)
    assert cli.main(["承認", "集計", "--承認者", "田中", "--はい", "--場所", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "（変更なし）" in out
    assert "ファイルは読み書きしません" in out
    assert "  （なし）" in out
    assert "実行の予定：手動（予定なし）" in out
    assert "次の実行" not in out and "使うルールデータ" not in out and "前回の承認からの変更" in out


def test_approve_without_previous_approval_has_no_diff_section(stub, tmp_path, capsys):
    stub.review_value = make_review(diff=None)
    cli.main(["承認", "集計", "--承認者", "田中", "--はい", "--場所", str(tmp_path)])
    assert "前回の承認からの変更" not in capsys.readouterr().out


def test_approve_refused_when_not_approvable(stub, tmp_path, capsys):
    stub.review_value = make_review(approvable=False, errors=["E3006 読めません", "ファイルがありません"])
    assert cli.main(["承認", "集計", "--承認者", "田中", "--はい", "--場所", str(tmp_path)], ask=never_asked) == 1
    captured = capsys.readouterr()
    assert "検査に通らないため、承認できません：" in captured.err
    assert "  E3006 読めません" in captured.err and "  ファイルがありません" in captured.err
    assert "このプログラムがすること" not in captured.out
    assert not any(call[0] == "approve" for call in stub.instances[0].calls)


def test_approve_already_approved(stub, tmp_path, capsys):
    stub.review_value = make_review(already_approved=True)
    assert cli.main(["承認", "集計", "--承認者", "田中", "--場所", str(tmp_path)], ask=never_asked) == 0
    assert "この内容はすでに承認されています。" in capsys.readouterr().out
    assert not any(call[0] == "approve" for call in stub.instances[0].calls)


@pytest.mark.parametrize("answer, approved", [("はい", True), (" y ", True), ("yes", True),
                                              ("いいえ", False), ("", False)])
def test_approve_confirmation_answers(stub, tmp_path, capsys, answer, approved):
    stub.review_value = make_review()
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return answer
    assert cli.main(["承認", "集計", "--承認者", "田中", "--場所", str(tmp_path)], ask=ask) == 0
    assert "「集計」を承認しますか？" in prompts[0]
    out = capsys.readouterr().out
    assert any(call[0] == "approve" for call in stub.instances[0].calls) is approved
    assert ("記録しました" in out) is approved
    assert ("承認しませんでした。" in out) is not approved


# ---------- 一覧 ----------

def test_list_empty(stub, tmp_path, capsys):
    assert cli.main(["一覧", "--場所", str(tmp_path)]) == 0
    assert "登録されたプログラムはありません" in capsys.readouterr().out


def test_list_shows_state_changes_and_last_run(stub, tmp_path, capsys):
    stub.statuses_value = [
        make_status(),
        make_status(program=SimpleNamespace(name="月次"), state="変更あり（承認待ち）", changes=["プログラム", "「会社休日.csv」"],
                    approval="田中", schedule="毎月25日 9時", next_run="2026年10月23日（金）09:00",
                    last=make_run(started_at="2026-10-14T09:00:00+09:00", status="失敗", late=True,
                                  error="明細.csv がありません")),
        make_status(program=SimpleNamespace(name="日次"), last=make_run(status="実行中")),
        make_status(program=SimpleNamespace(name="週次"), last=make_run(status="正常終了")),
    ]
    assert cli.main(["一覧", "--場所", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "【集計】承認済み\n  予定：手動（予定なし）\n  前回：（まだ実行していません）" in out
    assert "【月次】⚠ 変更あり（承認待ち）（プログラム、「会社休日.csv」）  最後の承認：田中" in out
    assert "  予定：毎月25日 9時  次の実行：2026年10月23日（金）09:00" in out
    assert "  前回：⚠ 2026-10-14 09:00 失敗（遅れて実行）：明細.csv がありません" in out
    assert "  前回：2026-10-14 10:05 実行中" in out and "  前回：2026-10-14 10:05 正常終了\n" in out


# ---------- 実行・履歴 ----------

def test_run_success_and_failure_exit_codes(stub, tmp_path, capsys):
    stub.run_value = make_run(log_path="実行記録/a.txt")
    assert cli.main(["実行", "集計", "--場所", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "2026-10-14 10:05 集計：正常終了" in out and "  実行記録：実行記録/a.txt" in out

    stub.run_value = make_run(status="承認待ち", error="まだ承認されていません")
    assert cli.main(["実行", "集計", "--場所", str(tmp_path)]) == 1
    assert "⚠ 2026-10-14 10:05 集計：承認待ち：まだ承認されていません" in capsys.readouterr().out


def test_print_run_notes_and_slot(capsys):
    cli._print_run(make_run(slot="2026-10-25T09:00:00+09:00", late=True, missed=2))
    out = capsys.readouterr().out
    assert out == "2026-10-25 09:00 集計：正常終了（遅れて実行、それより前の2回分は見送り）\n"
    cli._print_run(make_run(missed=1))
    assert "（それより前の1回分は見送り）" in capsys.readouterr().out


def test_history_empty_and_filtered(stub, tmp_path, capsys):
    assert cli.main(["履歴", "--場所", str(tmp_path)]) == 0
    assert "まだ実行していません" in capsys.readouterr().out
    stub.history_value = [make_run(), make_run(status="失敗", error="x")]
    assert cli.main(["履歴", "集計", "--場所", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert ("history", "集計") in stub.instances[1].calls
    assert out.count("集計：") == 2 and "⚠ " in out


# ---------- 起動 ----------

def test_start_once_reports_interrupted_and_runs_tick(stub, tmp_path, capsys):
    stub.interrupted_value = 2
    stub.tick_values = [make_run(slot="2026-10-14T09:00:00+09:00")]
    assert cli.main(["起動", "--一回", "--場所", str(tmp_path)], sleep=never_asked) == 0
    out = capsys.readouterr().out
    assert "前回途中で止まった実行を「中断」にしました：2件" in out
    assert "予定どおりに実行します" not in out
    assert "2026-10-14 09:00 集計：正常終了" in out
    assert stub.instances[0].calls == [("tick",)]


def test_start_loop_sleeps_between_ticks_until_interrupted(stub, tmp_path, capsys):
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise KeyboardInterrupt
    assert cli.main(["起動", "--場所", str(tmp_path)], sleep=sleep) == 0
    out = capsys.readouterr().out
    assert f"予定どおりに実行します（{cli.INTERVAL}秒ごとに確かめます。止めるときは Ctrl+C）" in out
    assert "前回途中で止まった" not in out
    assert sleeps == [cli.INTERVAL, cli.INTERVAL]
    assert stub.instances[0].calls.count(("tick",)) == 2
    assert out.rstrip().endswith("止めました。")


def test_start_refuses_second_instance(stub, tmp_path, capsys):
    from dandori_platform.lock import single_instance
    with single_instance(tmp_path / ".dandori" / "起動.lock"):
        assert cli.main(["起動", "--一回", "--場所", str(tmp_path)]) == 1
    assert "この作業フォルダの基盤はすでに起動しています" in capsys.readouterr().err
