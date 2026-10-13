"""命令行

    dandori 実行 ファイル.dandori        执行程序
    dandori 試し ファイル.dandori        执行程序，但不写出文件，报告"将要做什么"
    dandori 説明 ファイル.dandori        不执行，列出程序会读取、写出哪些文件
    dandori 字句 ファイル.dandori        断词结果（调试用）
    dandori 構文 ファイル.dandori        语法树（调试用）
    dandori 実行 -c "100と200を足して表示する。"   也可以直接传程序
    dandori 実行 ファイル.dandori --今日 2026年10月31日   重现某一天的执行（语法设计 13.7）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dandori import api, dates
from dandori.errors import Diagnostic, render
from dandori.frontend import ast
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context

COMMANDS = [
    ("実行", "プログラムを実行する"),
    ("試し", "試しに実行する（ファイルは書き出さない）"),
    ("説明", "プログラムが読み込む・書き出すファイルを表示する（実行しない）"),
    ("字句", "断詞の結果を表示する（デバッグ用）"),
    ("構文", "構文木を表示する（デバッグ用）"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dandori", description="段取り")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in COMMANDS:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("file", nargs="?", help=".dandori ファイル")
        sub.add_argument("-c", dest="code", help="プログラムを直接渡す")
        if name in ("実行", "試し"):
            sub.add_argument("--今日", dest="today", type=_parse_today,
                             help="「今日」の日付を指定する（例：2026年10月31日、2026/10/31）")

    args = parser.parse_args(argv)
    source = _load_source(args, parser)
    # 文件的相对路径以程序文件所在的目录为准；-c 时为当前目录
    base_dir = Path(args.file).resolve().parent if args.code is None else Path.cwd()

    if args.command in ("実行", "試し"):
        ctx = Context(base_dir=base_dir, dry_run=args.command == "試し")
        if args.today is not None:
            ctx.today = args.today
        errors = api.run(source, ctx)
        if not errors:
            _report_files(ctx)
    elif args.command == "説明":
        result = api.compile_source(source, base_dir=base_dir)
        errors = result.errors
        if not errors:
            _explain(result)
    elif args.command == "字句":
        tokens, errors = tokenize(source, Lexicon.from_registry(default_registry()))
        for token in tokens:
            line, col = source.line_col(token.span.start)
            print(f"{line:>3}:{col:<3} {token.describe()}")
    else:
        result = api.compile_source(source, base_dir=base_dir)
        print(ast.dump(result.tree))
        errors = result.errors

    _report(errors, source)
    return 1 if errors else 0


def _explain(result: api.CompileResult) -> None:
    if not result.resources:
        print("このプログラムはファイルを読み込んだり書き出したりしません")
        return
    print("このプログラムは次のことをします：")
    for resource in result.resources:
        note = "（条件によって）" if resource.conditional else ""
        print(f"  {resource.explain}{note}")


def _report_files(ctx: Context) -> None:
    sys.stdout.flush()          # 先输出程序的「表示する」，再输出文件的报告
    lines = ctx.transaction.report(done=not ctx.dry_run)
    if ctx.dry_run:
        print("（試し実行：ファイルは書き出していません）", file=sys.stderr)
    for line in lines:
        print(f"  {line}", file=sys.stderr)


def _report(errors: list[Diagnostic], source: Source) -> None:
    for error in errors:
        print(file=sys.stderr)
        print(render(error, source), file=sys.stderr)


def _parse_today(text: str):
    try:
        value = dates.parse_cell(text)
    except dates.InvalidDate:
        value = None
    if value is None:
        raise argparse.ArgumentTypeError(f"「{text}」は日付として読めません（例：2026年10月31日）")
    return value


def _load_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Source:
    if args.code is not None:
        return Source(args.code, "<-c>")
    if args.file is None:
        parser.error("ファイルか -c を指定してください")
    path = Path(args.file)
    return Source(path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    sys.exit(main())
