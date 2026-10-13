"""命令行：dandori-ai（AI助手设计.md 第 3 节）

    dandori-ai 作る "要望" --出力 月次.dandori
    dandori-ai 直す 月次.dandori
    dandori-ai 変える 月次.dandori "手数料を660円までにして"
    dandori-ai 評価 --ケース evals

公共选项：--提供元 claude|openai|deepseek、--モデル、--非対話、--サンプル
提供元的默认值来自环境变量 DANDORI_AI_PROVIDER（未设置时为 claude）。

审核用的信息（读写的文件、权限）来自编译器，不来自 AI 的自述。AI 永远不执行程序。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
from pathlib import Path

from dandori import api
from dandori.api import Source
from dandori_ai import evals
from dandori_ai.loop import GenerationFailed, run_session
from dandori_ai.providers import make_provider
from dandori_ai.providers.base import ProviderError
from dandori_ai.tools import Toolbox, explain


def main(argv: list[str] | None = None, provider_factory=None, ask=input) -> int:
    parser = argparse.ArgumentParser(prog="dandori-ai", description="段取り AI 助手")
    commands = parser.add_subparsers(dest="command", required=True)

    make = commands.add_parser("作る", help="要望からプログラムを作る")
    make.add_argument("request", help="日本語の要望")
    make.add_argument("--出力", dest="output", required=True, help="作るプログラムのファイル")
    fix = commands.add_parser("直す", help="検査に通らないプログラムを直す")
    fix.add_argument("file")
    change = commands.add_parser("変える", help="指示どおりにプログラムを変える")
    change.add_argument("file")
    change.add_argument("instruction", help="変えたい内容（日本語）")
    evaluate = commands.add_parser("評価", help="評価用のケースで AI を評価する")
    evaluate.add_argument("--ケース", dest="cases", default="evals")

    for sub in (make, fix, change, evaluate):
        sub.add_argument("--提供元", dest="provider", default=os.environ.get("DANDORI_AI_PROVIDER", "claude"))
        sub.add_argument("--モデル", dest="model")
    for sub in (make, fix, change):
        sub.add_argument("--非対話", dest="non_interactive", action="store_true",
                         help="質問しない（決められないことは「確認事項」として残す）")
        sub.add_argument("--サンプル", dest="samples", action="store_true",
                         help="CSV の先頭3行を AI に送ることを許可する（既定では列名と型だけ）")
    for sub in (fix, change):
        sub.add_argument("--はい", dest="yes", action="store_true", help="確認せずに書き込む")

    args = parser.parse_args(argv)
    factory = provider_factory or (lambda: make_provider(args.provider, args.model))
    try:
        provider = factory()
        if args.command == "評価":
            return _evaluate(provider, Path(args.cases))
        return _generate(args, provider, ask)
    except ProviderError as e:
        print(f"エラー：{e}", file=sys.stderr)
        return 1


def _generate(args, provider, ask) -> int:
    path = Path(args.output if args.command == "作る" else args.file)
    base_dir = path.resolve().parent
    if args.command == "作る":
        if path.exists():
            print(f"エラー：「{path}」はすでにあります（上書きしません）", file=sys.stderr)
            return 1
        before = None
        request = evals.build_request(args.request, base_dir, path.name)
    else:
        before = path.read_text(encoding="utf-8")
        request = _fix_request(before, base_dir) if args.command == "直す" else _change_request(before, args.instruction)
        if request is None:
            print("このプログラムは検査に通っています。直すところはありません。")
            return 0

    toolbox = Toolbox(base_dir, interactive=not args.non_interactive, allow_samples=args.samples,
                      ask=lambda q: ask(f"\n【質問】{q}\n> "))
    print("AI がプログラムを書いています…", file=sys.stderr)
    try:
        outcome = run_session(provider, toolbox, request)
    except GenerationFailed as e:
        print(f"エラー：{e}", file=sys.stderr)
        if e.last_errors:
            print(f"  最後のエラー：{'、'.join(e.last_errors)}", file=sys.stderr)
        return 1

    if before is not None:
        print(_diff(before, outcome.program, path.name))
        _report_permission_change(before, outcome.program, base_dir)
    _report(outcome, base_dir, path)
    if before is not None and not args.yes:
        if ask(f"\n「{path}」に書き込みますか？（はい／いいえ）> ").strip() not in ("はい", "y", "yes"):
            print("書き込みませんでした。")
            return 0
    path.write_text(outcome.program, encoding="utf-8")
    print(f"\n「{path}」に書きました。実行する前に、次のコマンドで確かめてください：")
    print(f"  dandori 説明 {path}")
    print(f"  dandori 試し {path}")
    return 0


def _fix_request(program: str, base_dir: Path) -> str | None:
    source = Source(program, "直す.dandori")
    result = api.compile_source(source, base_dir=base_dir)
    if not result.errors:
        return None
    errors = json.dumps(api.diagnostics(result.errors, source), ensure_ascii=False)
    return ("次のプログラムを、検査に通るように直してください。業務の意味は変えないでください。\n"
            f"```\n{program}\n```\n検査のエラー：{errors}")


def _change_request(program: str, instruction: str) -> str:
    return ("次のプログラムを、指示どおりに変えてください。指示にないところは変えないでください。\n"
            f"指示：{instruction}\n```\n{program}\n```")


def _diff(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), f"{name}（前）", f"{name}（後）",
                                 lineterm="")
    return "\n".join(lines) or "（変更なし）"


def _report_permission_change(before: str, after: str, base_dir: Path) -> None:
    """权限声明有变化：需要重新批准（语法设计 17.1）"""
    old = api.compile_source(Source(before), base_dir=base_dir).permissions or ()
    new = api.compile_source(Source(after), base_dir=base_dir).permissions or ()
    added, removed = set(new) - set(old), set(old) - set(new)
    if not added and not removed:
        return
    print("\n⚠ 「この処理が使うもの」が変わりました。改めて承認が必要です。")
    for kind, path, folder in sorted(added):
        print(f"  ＋ {kind}：「{path}」{'フォルダ' if folder else ''}")
    for kind, path, folder in sorted(removed):
        print(f"  － {kind}：「{path}」{'フォルダ' if folder else ''}")


def _report(outcome, base_dir: Path, path: Path) -> None:
    print(f"\n【概要】{outcome.summary}")
    if outcome.open_questions:
        print("【確認事項】決められなかった業務のルールです。確かめてから使ってください：")
        for question in outcome.open_questions:
            print(f"  ・{question}")
    # 编译器给出的事实（不是 AI 的自述）
    facts = explain(api.compile_source(Source(outcome.program, path.name), base_dir=base_dir))
    print("【このプログラムがすること】（段取りの検査による）")
    for line in facts["files"] or ["ファイルは読み書きしません"]:
        print(f"  {line}")
    if facts["rule_data"]:
        print("【使うルールデータ】")
        for line in facts["rule_data"]:
            print(f"  {line}")
    print(f"（AI とのやり取り：{outcome.turns}回、検査：{outcome.checks}回）")


def _evaluate(provider, folder: Path) -> int:
    cases = evals.load_cases(folder)
    if not cases:
        print(f"エラー：「{folder}」に評価のケースがありません", file=sys.stderr)
        return 1
    results = []
    for case in cases:
        print(f"評価中：{case['name']}…", file=sys.stderr)
        results.append(evals.run_case(case, provider))
    print(evals.report(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
