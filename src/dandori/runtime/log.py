"""执行日志（语法设计 17.2）

每次执行后写一份日志（日文文本 ＋ JSON），从不覆盖。需求文档 5.4："执行记录就是业务报告"。

记录：开始时间、所用时间、「今日」、程序的指纹、读取的文件（行数、指纹）、规则数据的版本、
      写出的文件、结果（正常终了或错误）。
不记录：数据本身（客户名、金额）。日志长期保存，看日志的人不一定有权限看客户数据。

有了这些信息就能重现任意一次执行：同样的程序、同样的输入（用指纹确认）、
同样的「今日」、同样的规则数据 → 同样的结果（架构设计 7.3）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import dandori
from dandori.dates import JST, format_western
from dandori.errors import Diagnostic
from dandori.frontend.source import Source


class ExecutionLog:
    def __init__(self, source: Source):
        self.source = source
        self.started = datetime.now(JST)
        self.program_sha256 = hashlib.sha256(source.text.encode("utf-8")).hexdigest()

    def finish(self, ctx, rule_data, errors: list[Diagnostic], seconds: float, executed: bool) -> dict:
        return {
            "dandori_version": dandori.__version__,
            "started_at": self.started.isoformat(timespec="seconds"),
            "duration_seconds": round(seconds, 3),
            "today": ctx.today.isoformat(),
            "program": {"name": Path(self.source.name).name, "sha256": self.program_sha256},
            "rule_data": list(rule_data),
            "reads": [{"file": f, "rows": rows, "sha256": sha} for f, rows, sha in ctx.reads],
            "writes": ctx.transaction.entries() if executed and not errors else [],
            "result": {
                "status": "ok" if not errors else "error",
                "executed": executed,
                "errors": [self._error(e) for e in errors],
            },
        }

    def _error(self, error: Diagnostic) -> dict:
        line, column = self.source.line_col(error.span.start)
        return {"code": error.code, "message": error.message, "line": line, "column": column}

    def write(self, record: dict, directory: Path) -> Path:
        """实行記録/2026-11-02_090015_入金消込.txt（和 .json）。同一秒内多次执行时加编号，从不覆盖"""
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{self.started:%Y-%m-%d_%H%M%S}_{Path(self.source.name).stem}"
        name, n = stem, 1
        while (directory / f"{name}.txt").exists() or (directory / f"{name}.json").exists():
            n += 1
            name = f"{stem}_{n}"
        text_path = directory / f"{name}.txt"
        text_path.write_text(render_text(record), encoding="utf-8")
        (directory / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return text_path


def render_text(record: dict) -> str:
    """给人看的日志"""
    started = datetime.fromisoformat(record["started_at"])
    today = datetime.fromisoformat(record["today"]).date()
    lines = [
        f"{format_western(started.date())} {started:%H:%M:%S} 実行（今日：{format_western(today)}）",
        f"プログラム：{record['program']['name']}（指紋：{record['program']['sha256'][:12]}）",
        f"段取り：{record['dandori_version']}",
    ]
    if record["rule_data"]:
        lines.append("ルールデータ：")
        lines += [f"  {item}" for item in record["rule_data"]]
    if record["reads"]:
        lines.append("読み込み：")
        lines += [f"  「{r['file']}」 {r['rows']}行（指紋：{r['sha256'][:12]}）" for r in record["reads"]]
    if record["writes"]:
        lines.append("書き出し：")
        lines += [f"  「{w['file']}」を{w['action']}（{w['rows']}行）" for w in record["writes"]]
    result = record["result"]
    seconds = record["duration_seconds"]
    if result["status"] == "ok":
        lines.append(f"結果：正常終了（{seconds}秒）")
    else:
        state = "途中でエラー" if result["executed"] else "エラーのため実行しませんでした"
        lines.append(f"結果：{state}（{seconds}秒）。ファイルは書き出していません")
        lines += [f"  [{e['code']}] {e['line']}行目：{e['message']}" for e in result["errors"]]
    return "\n".join(lines) + "\n"
