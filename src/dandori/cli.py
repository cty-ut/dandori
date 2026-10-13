"""命令行

    dandori 字句 ファイル.dandori        断词结果（调试用）
    dandori 構文 ファイル.dandori        语法树（调试用）
    dandori 構文 -c "100と200を足す。"   也可以直接传程序
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dandori.errors import Diagnostic, render
from dandori.frontend import ast
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.parser import parse
from dandori.frontend.source import Source
from dandori.plugin.registry import default_registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dandori", description="段取り")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [("字句", "断詞の結果を表示する（デバッグ用）"),
                            ("構文", "構文木を表示する（デバッグ用）")]:
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("file", nargs="?", help=".dandori ファイル")
        sub.add_argument("-c", dest="code", help="プログラムを直接渡す")

    args = parser.parse_args(argv)
    source = _load_source(args, parser)
    lexicon = Lexicon.from_registry(default_registry())

    tokens, errors = tokenize(source, lexicon)
    if args.command == "字句":
        for token in tokens:
            line, col = source.line_col(token.span.start)
            print(f"{line:>3}:{col:<3} {token.describe()}")
    else:
        program, parse_errors = parse(tokens, source)
        errors += parse_errors
        print(ast.dump(program))

    _report(errors, source)
    return 1 if errors else 0


def _report(errors: list[Diagnostic], source: Source) -> None:
    for error in sorted(errors, key=lambda e: e.span.start):
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
