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
    assert "⚠ 変更あり（承認待ち）　最後の承認：田中" in capsys.readouterr().out
    assert run_cli(["承認", "集計", "--承認者", "鈴木", "--はい"], tmp_path) == 0
    assert "+実行の予定は、「毎月20日" in capsys.readouterr().out


def test_cli_errors(tmp_path, folder, capsys):
    assert run_cli(["承認", "無い", "--承認者", "田中"], tmp_path) == 1
    assert "登録されていません" in capsys.readouterr().err


def test_platform_uses_only_public_api():
    root = Path(__file__).parent.parent / "src" / "dandori_platform"
    for path in root.glob("*.py"):
        tree = python_ast.parse(path.read_text(encoding="utf-8"))
        for node in python_ast.walk(tree):
            if isinstance(node, python_ast.ImportFrom) and node.module and node.module.startswith("dandori."):
                assert node.module == "dandori.api", f"{path.name}: {node.module}"
            if isinstance(node, python_ast.ImportFrom) and node.module and node.module.startswith("dandori_ai"):
                pytest.fail(f"{path.name} が AI 助手に依存しています")
