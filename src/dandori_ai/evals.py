"""评测（AI助手设计.md 第 7 节）

每个用例：需求 ＋ CSV ＋ 期望。检查项（expect 中写了的才检查）：

    file / header / rows / ordered   输出文件的列和内容
    output / numeric                 显示的内容（numeric：去掉「円」和千位分隔符后比较）
    must_ask                         需求模糊：必须提出確認事項，不能自己决定
    forbid                           程序中不能出现的写法（例：自己编出来的「消込の手数料」）
    read_only                        只能声明「読み込み」

另外对所有用例记录"权限是否最小"（声明了却没用的权限）。

结果保存在 evals/結果/〈提供者〉_〈模型〉_〈日時〉/：生成的程序、概要、確認事項、各项检查。
人要读一读生成的程序：段取り的卖点是"人能读懂"，这一点只有人能判断。
"""

from __future__ import annotations

import csv
import io
import json
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from dandori import api
from dandori.api import Context, Source
from dandori_ai.loop import GenerationFailed, run_session
from dandori_ai.providers.base import ProviderError
from dandori_ai.tools import Toolbox


@dataclass
class CaseResult:
    name: str
    category: str
    generated: bool
    correct: bool                        # 所有检查项都通过
    checks: list = field(default_factory=list)   # [(检查项, 是否通过, 说明)]
    minimal_permissions: bool | None = None
    turns: int = 0
    compiles: int = 0                    # 编译检查的次数
    seconds: float = 0.0
    models: list = field(default_factory=list)
    summary: str = ""
    open_questions: list = field(default_factory=list)
    program: str = ""
    note: str = ""


def load_cases(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def prepare(case: dict, directory: Path) -> None:
    for name, text in case["files"].items():
        (directory / name).write_text(text, encoding="utf-8")


def evaluate_program(case: dict, program: str, directory: Path) -> tuple[list, bool | None]:
    """程序本身的检查（与 AI 无关：标准答案程序也用它验证）。返回 (检查项, 权限是否最小)"""
    expect = case["expect"]
    checks = []
    compiled = api.compile_source(Source(program, "評価.dandori"), base_dir=directory)
    minimal = None if compiled.errors else not compiled.unused_permissions()

    for word in expect.get("forbid", []):
        checks.append(("禁止", word not in program, f"「{word}」を書いてはいけません" if word in program else ""))
    if expect.get("read_only"):
        kinds = {kind for kind, _, _ in compiled.permissions or ()}
        checks.append(("読むだけ", kinds <= {"読み込み"}, "" if kinds <= {"読み込み"} else f"宣言：{sorted(kinds)}"))

    if "output" in expect or "file" in expect:
        output = []
        ctx = Context(output=output.append, base_dir=directory, today=date.fromisoformat(case["today"]))
        errors = api.run(Source(program, "評価.dandori"), ctx)
        if errors:
            checks.append(("実行", False, f"実行エラー：{errors[0].message}"))
            return checks, minimal
        if "output" in expect:
            lines = [line for text in output for line in text.split("\n")]
            want = expect["output"]
            if expect.get("numeric"):
                lines, want = [_number(x) for x in lines], [_number(x) for x in want]
            checks.append(("表示", lines == want, "" if lines == want else f"表示：{lines}"))
        if "file" in expect:
            checks.append(("ファイル", *_check_file(expect, directory)))
    return checks, minimal


def _number(text: str) -> str:
    return re.sub(r"[円,¥\s]", "", text)


def _check_file(expect: dict, directory: Path) -> tuple[bool, str]:
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
    result = CaseResult(case["name"], case.get("category", ""), False, False)
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        prepare(case, directory)
        toolbox = Toolbox(directory, interactive=False)
        request = build_request(case["request"], directory, "評価.dandori")
        try:
            outcome = run_session(provider, toolbox, request)
        except (GenerationFailed, ProviderError) as e:
            result.compiles, result.seconds, result.note = toolbox.checks, time.monotonic() - started, str(e)
            return result
        checks, minimal = evaluate_program(case, outcome.program, directory)
        if case["expect"].get("must_ask"):
            asked = bool(outcome.open_questions)
            checks.append(("質問", asked, "" if asked else "決められないことを確認事項に挙げていません"))

    result.generated = True
    result.correct = all(ok for _, ok, _ in checks)
    result.checks = checks
    result.minimal_permissions = minimal
    result.turns, result.compiles = outcome.turns, outcome.checks
    result.seconds = time.monotonic() - started
    result.models = outcome.models
    result.summary, result.open_questions, result.program = outcome.summary, outcome.open_questions, outcome.program
    result.note = "；".join(note for _, ok, note in checks if not ok and note)
    return result


def build_request(request: str, directory: Path, program_name: str) -> str:
    files = "、".join(sorted(p.name for p in directory.glob("*.csv"))) or "（なし）"
    return (f"要望：\n{request}\n\nこのフォルダにあるファイル：{files}\n"
            f"作るプログラムのファイル名：{program_name}")


def run_all(cases: list[dict], provider, results_dir: Path | None = None, progress=None) -> tuple[list, Path | None]:
    results = []
    for case in cases:
        if progress:
            progress(case["name"])
        results.append(run_case(case, provider))
    saved = save(results, provider, results_dir) if results_dir is not None else None
    return results, saved


def save(results: list[CaseResult], provider, results_dir: Path) -> Path:
    """生成的程序、概要、確認事項、检查结果。人要读一读：可读性只有人能判断"""
    models = sorted({m for r in results for m in r.models}) or [getattr(provider, "model", "")]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = results_dir / f"{getattr(provider, 'name', 'provider')}_{'+'.join(models)}_{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    for r in results:
        if r.program:
            (folder / f"{r.name}.dandori").write_text(r.program, encoding="utf-8")
    (folder / "results.json").write_text(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
                                         encoding="utf-8")
    (folder / "report.txt").write_text(report(results), encoding="utf-8")
    return folder


def report(results: list[CaseResult]) -> str:
    models = sorted({m for r in results for m in r.models})
    lines = [f"モデル：{'、'.join(models) or '（不明）'}", "",
             f"{'ケース':<14}{'分類':<10}{'生成':<4}{'正解':<4}{'権限':<4}{'確認':<4}{'往復':<4}{'検査':<4}秒"]
    for r in results:
        perm = "－" if r.minimal_permissions is None else ("○" if r.minimal_permissions else "×")
        lines.append(f"{r.name:<14}{r.category:<10}{'○' if r.generated else '×':<4}{'○' if r.correct else '×':<4}"
                     f"{perm:<4}{len(r.open_questions):<4}{r.turns:<4}{r.compiles:<4}{r.seconds:.1f}  {r.note}")
    n = len(results) or 1
    by_category: dict[str, list] = {}
    for r in results:
        by_category.setdefault(r.category, []).append(r.correct)
    lines += ["",
              f"生成できた：{sum(r.generated for r in results)}/{len(results)}　"
              f"正解：{sum(r.correct for r in results)}/{len(results)}　"
              f"最小権限：{sum(bool(r.minimal_permissions) for r in results)}/{len(results)}　"
              f"平均検査回数：{sum(r.compiles for r in results) / n:.1f}",
              "分類別の正解：" + "、".join(f"{c} {sum(v)}/{len(v)}" for c, v in by_category.items())]
    return "\n".join(lines)
