"""命令行：dandori-platform（平台设计.md 第 5 节）

    dandori-platform 登録 入金消込/入金消込.dandori [--名前 入金消込]
    dandori-platform 承認 入金消込 --承認者 田中 [--はい]
    dandori-platform 一覧
    dandori-platform 実行 入金消込          手动运行（同样检查批准、记入履历）
    dandori-platform 履歴 [入金消込]
    dandori-platform 起動 [--一回]          常驻，每 30 秒检查一次；--一回 只检查一次（给 cron 等使用）

公共选项：--場所（工作文件夹，默认是当前目录）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from dandori_platform.lock import AlreadyRunning, single_instance
from dandori_platform.platform import Platform, PlatformError, Review
from dandori_platform.store import Run

INTERVAL = 30            # 常驻时每隔多少秒检查一次


def main(argv: list[str] | None = None, ask=input, clock=None, sleep=time.sleep) -> int:
    parser = argparse.ArgumentParser(prog="dandori-platform", description="段取りの基盤：予定・承認・実行履歴")
    commands = parser.add_subparsers(dest="command", required=True)

    register = commands.add_parser("登録", help="プログラムを基盤に登録する")
    register.add_argument("file", help=".dandori ファイル")
    register.add_argument("--名前", dest="name", help="基盤での名前（既定はファイル名）")
    approve = commands.add_parser("承認", help="プログラムの内容を確かめて承認する")
    approve.add_argument("name")
    approve.add_argument("--承認者", dest="approver", required=True)
    approve.add_argument("--はい", dest="yes", action="store_true", help="確認せずに承認する")
    commands.add_parser("一覧", help="登録したプログラムと状態を表示する")
    run = commands.add_parser("実行", help="今すぐ実行する（承認されていなければ実行しない）")
    run.add_argument("name")
    history = commands.add_parser("履歴", help="実行の履歴を表示する")
    history.add_argument("name", nargs="?")
    start = commands.add_parser("起動", help="予定どおりに実行する（常駐）")
    start.add_argument("--一回", dest="once", action="store_true", help="一度だけ確かめて終わる（cron などから使う）")
    for sub in commands.choices.values():
        sub.add_argument("--場所", dest="workspace", default=".", help="基盤の作業フォルダ（既定は今のフォルダ）")

    args = parser.parse_args(argv)
    platform = Platform(Path(args.workspace), **({"clock": clock} if clock else {}))
    try:
        if args.command == "登録":
            program = platform.register(Path(args.file), args.name)
            print(f"「{program.name}」を登録しました。動かす前に承認が必要です：")
            print(f"  dandori-platform 承認 {program.name} --承認者 〈名前〉")
        elif args.command == "承認":
            return _approve(platform, args, ask)
        elif args.command == "実行":
            result = platform.run(args.name)
            _print_run(result)
            return 0 if result.status == "正常終了" else 1
        elif args.command == "履歴":
            _history(platform, args.name)
        elif args.command == "起動":
            return _start(platform, args.once, sleep)
        else:
            _list(platform)
        return 0
    except PlatformError as e:
        print(f"エラー：{e}", file=sys.stderr)
        return 1
    finally:
        platform.close()


def _approve(platform: Platform, args, ask) -> int:
    review = platform.review(args.name)
    _show(review)
    if not review.approvable:
        print("\n検査に通らないため、承認できません：", file=sys.stderr)
        for error in review.errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    if review.already_approved:
        print("\nこの内容はすでに承認されています。")
        return 0
    if not args.yes:
        answer = ask(f"\nこの内容で「{review.program.name}」を承認しますか？（はい／いいえ）> ").strip()
        if answer not in ("はい", "y", "yes"):
            print("承認しませんでした。")
            return 0
    platform.approve(review, args.approver)
    print(f"{args.approver} さんの承認を記録しました。")
    return 0


def _show(review: Review) -> None:
    print(f"【{review.program.name}】{review.program.path}")
    if review.diff is not None:
        print("\n前回の承認からの変更：")
        print(review.diff or "（変更なし）")
    for path, diff in review.rule_diffs.items():
        print(f"\n⚠ 規則のファイル「{path}」が変わりました：")
        print(diff)
    if review.permissions_added or review.permissions_removed:
        print("\n⚠ 「この処理が使うもの」が変わりました：")
        for line in review.permissions_added:
            print(f"  ＋ {line}")
        for line in review.permissions_removed:
            print(f"  － {line}")
    if not review.approvable:
        return
    print("\nこのプログラムがすること（段取りの検査による）：")
    for line in review.files or ["ファイルは読み書きしません"]:
        print(f"  {line}")
    print("この処理が使うもの（宣言）：")
    for line in review.permissions or ["（なし）"]:
        print(f"  {line}")
    print(f"実行の予定：{review.schedule or '手動（予定なし）'}")
    if review.next_run:
        print(f"  次の実行：{review.next_run}")
    if review.rule_data:
        print("使うルールデータ：")
        for line in review.rule_data:
            print(f"  {line}")


def _list(platform: Platform) -> None:
    statuses = platform.statuses()
    if not statuses:
        print("登録されたプログラムはありません（dandori-platform 登録 〈ファイル〉）")
        return
    for s in statuses:
        mark = "" if s.state == "承認済み" else "⚠ "
        approval = f"　最後の承認：{s.approval}" if s.approval else ""
        changes = f"（{'、'.join(s.changes)}）" if s.changes else ""
        print(f"【{s.program.name}】{mark}{s.state}{changes}{approval}")
        print(f"  予定：{s.schedule}" + (f"　次の実行：{s.next_run}" if s.next_run else ""))
        if s.last is None:
            print("  前回：（まだ実行していません）")
        else:
            alert = "⚠ " if s.last.status not in ("正常終了", "実行中") else ""
            late = "（遅れて実行）" if s.last.late else ""
            print(f"  前回：{alert}{s.last.started_at[:16].replace('T', ' ')} {s.last.status}{late}"
                  + (f"：{s.last.error}" if s.last.error else ""))



def _start(platform: Platform, once: bool, sleep) -> int:
    """常驻：一个工作文件夹只能有一个（文件锁）。启动时把残留的「実行中」改为「中断」"""
    try:
        with single_instance(platform.workspace / ".dandori" / "起動.lock"):
            if interrupted := platform.interrupt_running():
                print(f"前回途中で止まった実行を「中断」にしました：{interrupted}件")
            if not once:
                print(f"予定どおりに実行します（{INTERVAL}秒ごとに確かめます。止めるときは Ctrl+C）")
            while True:
                for result in platform.tick():
                    _print_run(result)
                if once:
                    return 0
                sleep(INTERVAL)
    except AlreadyRunning:
        print("エラー：この作業フォルダの基盤はすでに起動しています", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("止めました。")
        return 0


def _print_run(run: Run) -> None:
    when = run.slot or run.started_at
    notes = []
    if run.late:
        notes.append("遅れて実行")
    if run.missed:
        notes.append(f"それより前の{run.missed}回分は見送り")
    note = f"（{'、'.join(notes)}）" if notes else ""
    mark = "" if run.status == "正常終了" else "⚠ "
    print(f"{mark}{when[:16].replace('T', ' ')} {run.program}：{run.status}{note}"
          + (f"：{run.error}" if run.error else ""))
    if run.log_path:
        print(f"  実行記録：{run.log_path}")


def _history(platform: Platform, name: str | None) -> None:
    runs = platform.history(name)
    if not runs:
        print("まだ実行していません")
        return
    for run in runs:
        _print_run(run)


if __name__ == "__main__":
    sys.exit(main())
