"""AI 可以使用的工具（AI助手设计.md 第 5 节）

工具名只能用英文字母（三家的 API 都这样规定），说明用日文。
所有工具都只通过公开 API（dandori.api）使用语言。

默认不发送客户数据：inspect_csv 只返回列名、类型、行数；允许时（--サンプル）才附最多 3 行。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from dandori import api
from dandori.api import Source


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict          # JSON Schema


def _schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


TOOLS = [
    ToolSpec("check_program", "段取りのプログラムを検査する。エラー（エラーコード・行・列・メッセージ）か、"
             "問題がなければプログラムが読み書きするファイルを返す。",
             _schema({"program": {"type": "string", "description": "段取りのプログラム全体"}})),
    ToolSpec("inspect_csv", "フォルダにある CSV ファイルの列名・型・行数を調べる。",
             _schema({"file": {"type": "string", "description": "ファイル名（例：明細.csv）"}})),
    ToolSpec("ask_user", "要望から決まらない業務のルールを、利用者に質問する。",
             _schema({"question": {"type": "string", "description": "利用者への質問（日本語、一つずつ）"}})),
    ToolSpec("submit_program", "できあがったプログラムを提出する。検査に通らなければ差し戻される。",
             _schema({
                 "program": {"type": "string", "description": "段取りのプログラム全体"},
                 "summary": {"type": "string", "description": "業務担当者向けの短い説明（日本語）"},
                 "open_questions": {"type": "array", "items": {"type": "string"},
                                    "description": "決められなかった業務のルール（なければ空）"},
             })),
]


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    submitted: dict | None = None      # submit_program が受け付けられたとき：program、summary、open_questions


@dataclass
class Toolbox:
    base_dir: Path
    interactive: bool = True
    allow_samples: bool = False
    ask: Callable[[str], str] = input
    checks: int = 0                               # 検査の回数（評価用）
    history: list = field(default_factory=list)   # 各回の検査で出たエラーコード（評価用）

    def execute(self, name: str, arguments: dict) -> ToolResult:
        if "__invalid__" in arguments:
            return ToolResult("引数が JSON として読めません。もう一度呼び出してください。", is_error=True)
        handler = {"check_program": self.check_program, "inspect_csv": self.inspect_csv,
                   "ask_user": self.ask_user, "submit_program": self.submit_program}.get(name)
        if handler is None:
            return ToolResult(f"「{name}」という道具はありません", is_error=True)
        try:
            return handler(**arguments)
        except TypeError as e:
            return ToolResult(f"引数が正しくありません：{e}", is_error=True)

    # ---------- 各工具 ----------

    def check_program(self, program: str) -> ToolResult:
        result, errors = self._compile(program)
        if errors:
            return ToolResult(json.dumps({"errors": errors}, ensure_ascii=False), is_error=True)
        return ToolResult(json.dumps({"ok": True, **explain(result)}, ensure_ascii=False))

    def inspect_csv(self, file: str) -> ToolResult:
        info = api.describe_csv(file, self.base_dir, sample_rows=3 if self.allow_samples else 0)
        return ToolResult(json.dumps(info, ensure_ascii=False), is_error="error" in info)

    def ask_user(self, question: str) -> ToolResult:
        if not self.interactive:
            return ToolResult("今は利用者に質問できません。業務のルールを勝手に決めず、"
                              "提出するときに open_questions に書いてください。", is_error=True)
        return ToolResult(self.ask(question) or "（回答なし）")

    def submit_program(self, program: str, summary: str, open_questions: list[str]) -> ToolResult:
        result, errors = self._compile(program)
        if errors:
            return ToolResult(json.dumps({"errors": errors, "note": "検査に通らないため差し戻しました"},
                                         ensure_ascii=False), is_error=True)
        if result.permissions is None:
            return ToolResult("プログラムの最初に「この処理が使うもの」を書いてください。", is_error=True)
        return ToolResult("受け付けました。", submitted={
            "program": program, "summary": summary, "open_questions": list(open_questions)})

    def _compile(self, program: str):
        self.checks += 1
        source = Source(program, "生成中.dandori")
        result = api.compile_source(source, base_dir=self.base_dir)
        errors = api.diagnostics(result.errors, source)
        self.history.append([e["code"] for e in errors])
        return result, errors


def explain(result: api.CompileResult) -> dict:
    """编译器给出的事实（不是 AI 的自述）：读写的文件、权限、规则数据"""
    return {
        "files": [r.explain + ("（条件によって）" if r.conditional else "") for r in result.resources],
        "permissions": [f"{k}：「{p}」{'フォルダ' if folder else ''}" for k, p, folder in result.permissions or []],
        "rule_data": list(result.rule_data),
    }
