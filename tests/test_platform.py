"""阶段 8：平台（平台设计.md）"""

import ast as python_ast
from datetime import datetime
from pathlib import Path

import pytest

from dandori.dates import JST
from dandori_platform import cli
from dandori_platform.platform import Platform, PlatformError

PROGRAM = """この処理が使うもの：
  読み込み：「明細.csv」
  書き出し：「出力」フォルダ
ここまで。
実行の予定は、「毎月25日（休日なら前営業日） 9時」とする。
「明細.csv」を読み込んで、明細とする。
明細の金額を取引先ごとに合計して、「出力/集計.csv」に書き出す。
"""


def at(y, m, d, hour=0, minute=0):
    return datetime(y, m, d, hour, minute, tzinfo=JST)


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@pytest.fixture
def folder(tmp_path):
    work = tmp_path / "集計"
    work.mkdir()
    (work / "明細.csv").write_text("取引先,金額\n山田商事,1000\n佐藤物産,2000\n", encoding="utf-8")
    (work / "集計.dandori").write_text(PROGRAM, encoding="utf-8")
    return work


@pytest.fixture
def platform(tmp_path):
    p = Platform(tmp_path, clock=Clock(at(2026, 10, 14, 10)))
    yield p
    p.close()


def edit(path: Path, old: str, new: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


# ---------- 登録 ----------

def test_register(platform, folder):
    program = platform.register(folder / "集計.dandori")
    assert program.name == "集計" and program.path == folder / "集計.dandori"
    [status] = platform.statuses()
    assert status.state == "未承認"
    assert status.schedule == "毎月25日 9時00分（休日なら前営業日）"
    assert status.next_run == "2026年10月23日（金）09:00"
    assert status.last is None


def test_register_rejects(platform, folder):
    with pytest.raises(PlatformError, match="ありません"):
        platform.register(folder / "無い.dandori")
    edit(folder / "集計.dandori", "この処理が使うもの：\n  読み込み：「明細.csv」\n  書き出し：「出力」フォルダ\nここまで。\n", "")
    with pytest.raises(PlatformError, match="この処理が使うもの"):
        platform.register(folder / "集計.dandori")
    (folder / "壊れ.dandori").write_text("100を表示する", encoding="utf-8")
    with pytest.raises(PlatformError, match="検査に通りません"):
        platform.register(folder / "壊れ.dandori")


def test_register_twice(platform, folder):
    platform.register(folder / "集計.dandori")
    with pytest.raises(PlatformError, match="すでに登録"):
        platform.register(folder / "集計.dandori")
    (folder / "別").mkdir()
    (folder / "別" / "集計.dandori").write_text("この処理が使うもの：\nここまで。\n1を表示する。\n", encoding="utf-8")
    with pytest.raises(PlatformError, match="名前はすでに使われています"):
        platform.register(folder / "別" / "集計.dandori")
    assert platform.register(folder / "別" / "集計.dandori", name="集計2").name == "集計2"


def test_persists(tmp_path, folder):
    first = Platform(tmp_path)
    first.register(folder / "集計.dandori")
    first.close()
    second = Platform(tmp_path)
    assert [p.name for p in second.store.programs()] == ["集計"]
    second.close()


# ---------- 承認 ----------

def test_review_and_approve(platform, folder):
    platform.register(folder / "集計.dandori")
    review = platform.review("集計")
    assert review.approvable and not review.already_approved and review.diff is None
    assert review.files == ["「明細.csv」を読み込みます", "「出力/集計.csv」を新規作成します"]
    assert review.permissions == ["読み込み：「明細.csv」", "書き出し：「出力」フォルダ"]
    assert review.schedule == "毎月25日 9時00分（休日なら前営業日）"
    assert any(line.startswith("祝日") for line in review.rule_data)
    platform.approve(review, "田中")
    [status] = platform.statuses()
    assert status.state == "承認済み" and status.approval == "田中（2026-10-14 10:00）"
    assert platform.review("集計").already_approved


def test_change_requires_new_approval(platform, folder):
    platform.register(folder / "集計.dandori")
    platform.approve(platform.review("集計"), "田中")
    edit(folder / "集計.dandori", "毎月25日", "毎月20日")         # 改予定也是改程序
    assert platform.statuses()[0].state == "変更あり（承認待ち）"
    review = platform.review("集計")
    assert "-実行の予定は、「毎月25日" in review.diff and "+実行の予定は、「毎月20日" in review.diff
    assert review.permissions_added == review.permissions_removed == []
    platform.approve(review, "鈴木")
    assert platform.statuses()[0].state == "承認済み"
    # 改回以前批准过的版本：有效的只有最后一次批准
    edit(folder / "集計.dandori", "毎月20日", "毎月25日")
    assert platform.statuses()[0].state == "変更あり（承認待ち）"


def test_permission_change_is_highlighted(platform, folder):
    platform.register(folder / "集計.dandori")
    platform.approve(platform.review("集計"), "田中")
    edit(folder / "集計.dandori", "  書き出し：「出力」フォルダ\n", "  書き出し：「出力」フォルダ\n  上書き：「明細.csv」\n")
    review = platform.review("集計")
    assert review.permissions_added == ["上書き：「明細.csv」"]


def test_approve_what_was_reviewed(platform, folder):
    # 审核之后、批准之前文件被改了：批准的是人看到的那份
    platform.register(folder / "集計.dandori")
    review = platform.review("集計")
    edit(folder / "集計.dandori", "毎月25日", "毎月1日")
    platform.approve(review, "田中")
    assert platform.statuses()[0].state == "変更あり（承認待ち）"


def test_cannot_approve_broken(platform, folder):
    platform.register(folder / "集計.dandori")
    edit(folder / "集計.dandori", "明細の金額", "明細の金がく")
    review = platform.review("集計")
    assert not review.approvable and review.errors
    with pytest.raises(PlatformError):
        platform.approve(review, "田中")
    assert platform.statuses()[0].schedule == "（検査エラー）"
    with pytest.raises(PlatformError, match="承認者"):
        platform.approve(platform.review("集計"), " ")


def test_missing_file_and_unknown_name(platform, folder):
    platform.register(folder / "集計.dandori")
    (folder / "集計.dandori").unlink()
    assert platform.statuses()[0].state == "ファイルなし"
    with pytest.raises(PlatformError, match="登録されていません"):
        platform.review("無い")


# ---------- 命令行 ----------

def run_cli(args, tmp_path, answers=()):
    replies = iter(answers)
    return cli.main([*args, "--場所", str(tmp_path)], ask=lambda _: next(replies),
                    clock=Clock(at(2026, 10, 14, 10)))


def test_cli_flow(tmp_path, folder, capsys):
    assert run_cli(["一覧"], tmp_path) == 0
    assert "登録されたプログラムはありません" in capsys.readouterr().out
    assert run_cli(["登録", str(folder / "集計.dandori")], tmp_path) == 0
    assert run_cli(["承認", "集計", "--承認者", "田中"], tmp_path, ["いいえ"]) == 0
    assert "承認しませんでした" in capsys.readouterr().out
    assert run_cli(["承認", "集計", "--承認者", "田中"], tmp_path, ["はい"]) == 0
    out = capsys.readouterr().out
    assert "「出力/集計.csv」を新規作成します" in out and "次の実行：2026年10月23日（金）09:00" in out
    assert run_cli(["承認", "集計", "--承認者", "田中"], tmp_path) == 0
    assert "すでに承認されています" in capsys.readouterr().out
    edit(folder / "集計.dandori", "毎月25日", "毎月20日")
    assert run_cli(["一覧"], tmp_path) == 0
    assert "⚠ 変更あり（承認待ち）（プログラム）　最後の承認：田中" in capsys.readouterr().out
    assert run_cli(["承認", "集計", "--承認者", "鈴木", "--はい"], tmp_path) == 0
    assert "+実行の予定は、「毎月20日" in capsys.readouterr().out


def test_cli_errors(tmp_path, folder, capsys):
    assert run_cli(["承認", "無い", "--承認者", "田中"], tmp_path) == 1
    assert "登録されていません" in capsys.readouterr().err


# ---------- 実行（8-3） ----------

def approved(platform, folder, who="田中"):
    platform.register(folder / "集計.dandori")
    platform.approve(platform.review("集計"), who)


def log_record(run):
    import json
    return json.loads(Path(run.log_path).with_suffix(".json").read_text(encoding="utf-8"))


def test_manual_run(platform, folder):
    platform.register(folder / "集計.dandori")
    first = platform.run("集計")
    assert first.status == "承認待ち" and first.error == "まだ承認されていません"
    assert not (folder / "出力").exists()
    platform.approve(platform.review("集計"), "田中")
    second = platform.run("集計")
    assert second.status == "正常終了" and second.slot is None and not second.late
    assert (folder / "出力" / "集計.csv").exists()
    assert log_record(second)["today"] == "2026-10-14"
    # 手动运行可以重复；这次因为文件已经存在而出错，错误记入履历
    third = platform.run("集計")
    assert third.status == "エラー" and "集計.csv" in third.error
    assert [r.status for r in platform.history("集計")] == ["エラー", "正常終了", "承認待ち"]


def test_run_saves_display_output(platform, folder):
    edit(folder / "集計.dandori", "「出力/集計.csv」に書き出す。", "表示する。")
    edit(folder / "集計.dandori", "  書き出し：「出力」フォルダ\n", "")
    approved(platform, folder)
    run = platform.run("集計")
    assert run.status == "正常終了"
    shown = Path(run.log_path).with_name(Path(run.log_path).stem + "_表示.txt").read_text(encoding="utf-8")
    assert "山田商事" in shown


def test_changed_program_is_not_run(platform, folder):
    approved(platform, folder)
    edit(folder / "集計.dandori", "毎月25日", "毎月20日")
    run = platform.run("集計")
    assert run.status == "承認待ち" and run.error == "承認の後に変更されています：プログラム"
    assert not (folder / "出力").exists()


def test_missing_file_is_recorded(platform, folder):
    approved(platform, folder)
    (folder / "集計.dandori").unlink()
    run = platform.run("集計")
    assert run.status == "エラー" and "ありません" in run.error


# ---------- 調度（8-3） ----------

def test_tick_runs_on_schedule(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    assert platform.tick() == []
    clock.moment = at(2026, 10, 23, 9)                 # 25日是星期日 → 23日（金）
    [run] = platform.tick()
    assert run.status == "正常終了" and run.slot == "2026-10-23T09:00:00+09:00"
    assert not run.late and run.missed == 0
    assert log_record(run)["today"] == "2026-10-23"
    clock.moment = at(2026, 10, 23, 9, 10)
    assert platform.tick() == []                       # 同一个预定时间只运行一次
    platform.close()


def test_catch_up_once_with_slot_date_as_today(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    clock.moment = at(2026, 12, 28, 10)                # 10/23、11/25、12/25 都错过了
    [run] = platform.tick()
    assert run.slot == "2026-12-25T09:00:00+09:00" and run.late and run.missed == 2
    assert log_record(run)["today"] == "2026-12-25"    # 「今日」是预定的日子
    assert platform.tick() == []
    platform.close()


def test_slots_before_approval_are_not_run(tmp_path, folder):
    clock = Clock(at(2026, 10, 23, 10))                # 当天 9 点之后才批准
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    assert platform.tick() == []
    clock.moment = at(2026, 11, 25, 9, 1)
    assert [r.slot for r in platform.tick()] == ["2026-11-25T09:00:00+09:00"]
    platform.close()


def test_waiting_for_approval_is_not_caught_up(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    edit(folder / "集計.dandori", "取引先ごとに", "取引先ごとに")      # 内容不变
    edit(folder / "集計.dandori", "明細とする。", "明細とする。\n")   # 改了一个字
    clock.moment = at(2026, 10, 23, 9)
    [run] = platform.tick()
    assert run.status == "承認待ち"
    clock.moment = at(2026, 10, 23, 12)
    platform.approve(platform.review("集計"), "田中")
    assert platform.tick() == []                       # 批准了也不补
    platform.close()


def test_schedule_follows_approved_version(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    edit(folder / "集計.dandori", "毎月25日", "毎月15日")   # 没有批准：按批准的 25 日
    clock.moment = at(2026, 10, 15, 9, 30)
    assert platform.tick() == []
    clock.moment = at(2026, 10, 23, 9)
    assert [r.status for r in platform.tick()] == ["承認待ち"]
    platform.close()


def test_missing_data_is_an_error_not_silence(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    platform = Platform(tmp_path, clock=clock)
    approved(platform, folder)
    (folder / "明細.csv").unlink()
    clock.moment = at(2026, 10, 23, 9)
    [run] = platform.tick()
    assert run.status == "エラー" and "明細.csv" in run.error
    platform.close()


def test_two_platforms_do_not_run_twice(tmp_path, folder):
    clock = Clock(at(2026, 10, 14, 10))
    first = Platform(tmp_path, clock=clock)
    approved(first, folder)
    second = Platform(tmp_path, clock=clock)
    clock.moment = at(2026, 10, 23, 9)
    assert len(first.tick()) == 1 and second.tick() == []
    first.close()
    second.close()


def test_interrupted_runs(platform, folder):
    approved(platform, folder)
    platform.store.start_run("集計", None, "2026-10-14T09:00:00+09:00", None)
    assert platform.interrupt_running() == 1
    assert platform.history("集計")[0].status == "中断"


# ---------- 規則のファイル（8-4） ----------

RULED = """この処理が使うもの：
  読み込み：「会社休日.csv」
ここまで。
暦の会社休日は「会社休日.csv」とする。
実行の予定は、「毎月第3会社営業日 9時」とする。
今日+1会社営業日を表示する。
"""


@pytest.fixture
def ruled(tmp_path):
    work = tmp_path / "暦"
    work.mkdir()
    (work / "会社休日.csv").write_text("日付\n2026/11/02\n", encoding="utf-8")
    (work / "暦.dandori").write_text(RULED, encoding="utf-8")
    return work


def test_rule_file_change_requires_approval(platform, ruled):
    platform.register(ruled / "暦.dandori")
    review = platform.review("暦")
    assert review.snapshot.rule_files["会社休日.csv"]["sha256"]
    platform.approve(review, "田中")
    assert platform.statuses()[0].state == "承認済み"
    assert platform.statuses()[0].next_run == "2026年11月6日（金）09:00"

    (ruled / "会社休日.csv").write_text("日付\n2026/11/02\n2026/11/04\n", encoding="utf-8")
    status = platform.statuses()[0]
    assert status.state == "変更あり（承認待ち）" and status.changes == ["「会社休日.csv」"]
    run = platform.run("暦")
    assert run.status == "承認待ち" and "「会社休日.csv」" in run.error
    review = platform.review("暦")
    assert review.changes == ["「会社休日.csv」"] and review.diff == ""
    assert "+2026/11/04" in review.rule_diffs["会社休日.csv"]
    platform.approve(review, "鈴木")
    assert platform.run("暦").status == "正常終了"


def test_missing_rule_file_cannot_be_approved(platform, ruled):
    platform.register(ruled / "暦.dandori")
    (ruled / "会社休日.csv").unlink()
    review = platform.review("暦")
    assert not review.approvable and any("会社休日.csv" in e for e in review.errors)


def test_shift_jis_rule_file_diff(platform, ruled):
    platform.register(ruled / "暦.dandori")
    platform.approve(platform.review("暦"), "田中")
    (ruled / "会社休日.csv").write_bytes("日付,名前\n2026/11/02,創立記念日\n".encode("cp932"))
    assert "創立記念日" in platform.review("暦").rule_diffs["会社休日.csv"]


# ---------- 命令行（8-3） ----------

def test_cli_run_history_and_start(tmp_path, folder, capsys):
    assert run_cli(["登録", str(folder / "集計.dandori")], tmp_path) == 0
    assert run_cli(["実行", "集計"], tmp_path) == 1
    assert "⚠ " in capsys.readouterr().out
    assert run_cli(["承認", "集計", "--承認者", "田中", "--はい"], tmp_path) == 0
    capsys.readouterr()
    assert run_cli(["起動", "--一回"], tmp_path) == 0
    assert "集計" not in capsys.readouterr().out      # 还没到时间
    assert run_cli(["実行", "集計"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "集計：正常終了" in out and "実行記録：" in out
    assert run_cli(["履歴"], tmp_path) == 0
    out = capsys.readouterr().out
    assert "集計：正常終了" in out and "⚠ " in out and "承認待ち：まだ承認されていません" in out


def test_cli_start_loop_and_lock(tmp_path, folder, capsys):
    from dandori_platform.lock import single_instance
    with single_instance(tmp_path / ".dandori" / "起動.lock"):
        assert run_cli(["起動", "--一回"], tmp_path) == 1
    assert "すでに起動しています" in capsys.readouterr().err

    def stop(_):
        raise KeyboardInterrupt
    assert cli.main(["起動", "--場所", str(tmp_path)], sleep=stop, clock=Clock(at(2026, 10, 14, 10))) == 0
    assert "止めました" in capsys.readouterr().out


def test_platform_uses_only_public_api():
    root = Path(__file__).parent.parent / "src" / "dandori_platform"
    for path in root.glob("*.py"):
        tree = python_ast.parse(path.read_text(encoding="utf-8"))
        for node in python_ast.walk(tree):
            if isinstance(node, python_ast.ImportFrom) and node.module and node.module.startswith("dandori."):
                assert node.module == "dandori.api", f"{path.name}: {node.module}"
            if isinstance(node, python_ast.ImportFrom) and node.module and node.module.startswith("dandori_ai"):
                pytest.fail(f"{path.name} が AI 助手に依存しています")


def test_excel_rule_file_diff(platform, tmp_path):
    import openpyxl
    work = tmp_path / "xl"
    work.mkdir()

    def save(rows):
        wb = openpyxl.Workbook()
        wb.active.title = "休日"
        for row in rows:
            wb.active.append(row)
        wb.save(work / "会社休日.xlsx")

    save([["日付"], [datetime(2026, 11, 2)]])
    (work / "暦.dandori").write_text(RULED.replace("会社休日.csv", "会社休日.xlsx"), encoding="utf-8")
    platform.register(work / "暦.dandori")
    platform.approve(platform.review("暦"), "田中")
    save([["日付"], [datetime(2026, 11, 2)], [datetime(2026, 11, 4)]])
    review = platform.review("暦")
    assert review.changes == ["「会社休日.xlsx」"]
    assert "+2026/11/04" in review.rule_diffs["会社休日.xlsx"]       # Excel でも中身の差分が読める
