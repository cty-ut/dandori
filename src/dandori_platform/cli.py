"""命令行：dandori-platform（平台设计.md 第 5 节）

    dandori-platform 登録 入金消込/入金消込.dandori [--名前 入金消込]
    dandori-platform 承認 入金消込 --承認者 田中 [--はい]
    dandori-platform 一覧

公共选项：--場所（工作文件夹，默认是当前目录）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dandori_platform.platform import Platform, PlatformError, Review


def main(argv: list[str] | None = None, ask=input, clock=None) -> int:
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
        print(f"【{s.program.name}】{mark}{s.state}{approval}")
        print(f"  予定：{s.schedule}" + (f"　次の実行：{s.next_run}" if s.next_run else ""))
        if s.last is None:
            print("  前回：（まだ実行していません）")
        else:
            alert = "⚠ " if s.last.status not in ("正常終了", "実行中") else ""
            late = "（遅れて実行）" if s.last.late else ""
            print(f"  前回：{alert}{s.last.started_at[:16].replace('T', ' ')} {s.last.status}{late}"
                  + (f"：{s.last.error}" if s.last.error else ""))


if __name__ == "__main__":
    sys.exit(main())
