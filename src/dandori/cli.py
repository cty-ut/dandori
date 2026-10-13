"""命令行

    dandori 実行 ファイル.dandori        执行程序
    dandori 字句 ファイル.dandori        断词结果（调试用）
    dandori 構文 ファイル.dandori        语法树（调试用）
    dandori 実行 -c "100と200を足して表示する。"   也可以直接传程序
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dandori import api
from dandori.errors import Diagnostic, render
from dandori.frontend import ast
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.source import Source
from dandori.plugin.registry import default_registry
from dandori.runtime.interpreter import Context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dandori", description="段取り")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("実行", "プログラムを実行する"),
                            ("字句", "断詞の結果を表示する（デバッグ用）"),
                            ("構文", "構文木を表示する（デバッグ用）")]:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("file", nargs="?", help=".dandori ファイル")
        sub.add_argument("-c", dest="code", help="プログラムを直接渡す")

    args = parser.parse_args(argv)
    source = _load_source(args, parser)
    # 「読み込む」的相对路径以程序文件所在的目录为准；-c 时为当前目录
    base_dir = Path(args.file).resolve().parent if args.code is None else Path.cwd()

    if args.command == "実行":
        errors = api.run(source, Context(base_dir=base_dir))
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


def _report(errors: list[Diagnostic], source: Source) -> None:
    for error in errors:
        print(file=sys.stderr)
        print(render(error, source), file=sys.stderr)


def _load_source(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Source:
    if args.code is not None:
        return Source(args.code, "<-c>")
    if args.file is None:
        parser.error("ファイルか -c を指定してください")
    path = Path(args.file)
    return Source(path.read_text(encoding="utf-8"), str(path))


if __name__ == "__main__":
    sys.exit(main())
