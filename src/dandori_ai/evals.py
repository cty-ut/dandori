"""评测（AI助手设计.md 第 7 节）

每个用例：需求 ＋ CSV ＋ 期望的输出文件。对一个提供者测量：
编译通过率、平均纠错次数、执行结果是否正确、所用时间。
"""

from __future__ import annotations

import csv
import io
import json
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dandori import api
from dandori.api import Context, Source
from dandori_ai.loop import GenerationFailed, run_session
from dandori_ai.providers.base import ProviderError
from dandori_ai.tools import Toolbox


@dataclass
class CaseResult:
    name: str
    generated: bool
    correct: bool
    turns: int
    checks: int
    seconds: float
    note: str = ""


def load_cases(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def prepare(case: dict, directory: Path) -> None:
    for name, text in case["files"].items():
        (directory / name).write_text(text, encoding="utf-8")


def check_output(case: dict, program: str, directory: Path) -> tuple[bool, str]:
    """程序在用例的数据上执行，输出文件与期望一致吗"""
    output = []
    ctx = Context(output=output.append, base_dir=directory, today=date.fromisoformat(case["today"]))
    errors = api.run(Source(program, "評価.dandori"), ctx)
    if errors:
        return False, f"実行エラー：{errors[0].message}"
    expect = case["expect"]
    path = directory / expect["file"]
    if not path.exists():
        return False, f"「{expect['file']}」が書き出されていません"
    rows = list(csv.reader(io.StringIO(path.read_bytes().decode("utf-8-sig"))))
    header, body = rows[0], rows[1:]
    if header != expect["header"]:
        return False, f"列が違います：{header}"
    same = body == expect["rows"] if expect.get("ordered") else sorted(body) == sorted(expect["rows"])
    return (True, "") if same else (False, f"内容が違います：{body}")


def run_case(case: dict, provider) -> CaseResult:
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        prepare(case, directory)
        toolbox = Toolbox(directory, interactive=False)
        request = build_request(case["request"], directory, "評価.dandori")
        try:
            outcome = run_session(provider, toolbox, request)
        except (GenerationFailed, ProviderError) as e:
            return CaseResult(case["name"], False, False, 0, toolbox.checks, time.monotonic() - started, str(e))
        correct, note = check_output(case, outcome.program, directory)
        return CaseResult(case["name"], True, correct, outcome.turns, outcome.checks,
                          time.monotonic() - started, note)


def build_request(request: str, directory: Path, program_name: str) -> str:
    files = "、".join(sorted(p.name for p in directory.glob("*.csv"))) or "（なし）"
    return (f"要望：\n{request}\n\nこのフォルダにあるファイル：{files}\n"
            f"作るプログラムのファイル名：{program_name}")


def report(results: list[CaseResult]) -> str:
    lines = [f"{'ケース':<16}{'生成':<6}{'正解':<6}{'往復':<6}{'検査':<6}秒"]
    for r in results:
        lines.append(f"{r.name:<16}{'○' if r.generated else '×':<6}{'○' if r.correct else '×':<6}"
                     f"{r.turns:<6}{r.checks:<6}{r.seconds:.1f}  {r.note}")
    n = len(results) or 1
    lines.append(f"生成できた：{sum(r.generated for r in results)}/{len(results)}　"
                 f"正解：{sum(r.correct for r in results)}/{len(results)}　"
                 f"平均検査回数：{sum(r.checks for r in results) / n:.1f}")
    return "\n".join(lines)
