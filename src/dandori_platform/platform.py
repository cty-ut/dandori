"""平台的核心（平台设计.md 第 5、6 节）。命令行（cli.py）只负责显示和确认

只通过公开 API（dandori.api）使用语言。
审核用的信息都来自编译器，不来自作者或 AI 的自述。
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from dandori import api
from dandori.api import JST, Source
from dandori_platform.store import Program, Run, Store


class PlatformError(Exception):
    """给用户看的错误（日文）"""


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Review:
    """批准前给人看的内容"""
    program: Program
    text: str                          # 审核的那份文本：批准的就是它
    sha256: str
    errors: list[str]                  # 编译错误（有错误时不能批准）
    files: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    schedule: str | None = None
    next_run: str | None = None
    rule_data: list[str] = field(default_factory=list)
    diff: str | None = None            # 与上次批准的版本的差异；第一次批准时为 None
    permissions_added: list[str] = field(default_factory=list)
    permissions_removed: list[str] = field(default_factory=list)
    already_approved: bool = False

    @property
    def approvable(self) -> bool:
        return not self.errors


@dataclass
class Status:
    """一覧的一行"""
    program: Program
    state: str                         # 承認済み / 未承認 / 変更あり（承認待ち） / ファイルなし
    schedule: str
    next_run: str | None
    last: Run | None
    approval: str | None = None        # 「田中（2026-10-14 09:00）」


class Platform:
    def __init__(self, workspace: Path, clock=lambda: datetime.now(JST)):
        self.workspace = workspace.resolve()
        self.store = Store(self.workspace)
        self.clock = clock

    def close(self) -> None:
        self.store.close()

    def now(self) -> datetime:
        return self.clock().astimezone(JST)

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---------- 登録 ----------

    def register(self, path: Path, name: str | None = None) -> Program:
        path = path.resolve()
        if not path.is_file():
            raise PlatformError(f"「{path}」がありません")
        name = name or path.stem
        if existing := self.store.program_by_path(path):
            raise PlatformError(f"「{path.name}」は「{existing.name}」という名前ですでに登録されています")
        if self.store.program(name):
            raise PlatformError(f"「{name}」という名前はすでに使われています（--名前 で別の名前をつけてください）")
        result = self._compile(path, path.read_text(encoding="utf-8"))
        if result.errors:
            raise PlatformError(f"検査に通りません：{result.errors[0].message}（dandori 説明 {path.name} で確かめてください）")
        if result.permissions is None:
            raise PlatformError("「この処理が使うもの」が書かれていません。基盤で動かすプログラムには必要です")
        self.store.add_program(name, path, self._stamp())
        return self.store.program(name)

    # ---------- 承認 ----------

    def review(self, name: str) -> Review:
        program = self._program(name)
        if not program.path.is_file():
            raise PlatformError(f"「{program.path}」がありません")
        text = program.path.read_text(encoding="utf-8")
        sha = fingerprint(text)
        result = self._compile(program.path, text)
        review = Review(program, text, sha, [e.message for e in result.errors])
        approval = self.store.latest_approval(name)
        if approval is not None:
            review.already_approved = approval.sha256 == sha
            review.diff = _diff(approval.text, text, program.path.name)
        if result.errors:
            return review
        if result.permissions is None:
            review.errors.append("「この処理が使うもの」が書かれていません。基盤で動かすプログラムには必要です")
            return review

        review.files = [r.explain + ("（条件によって）" if r.conditional else "") for r in result.resources]
        review.permissions = [_permission(p) for p in result.permissions]
        review.rule_data = list(result.rule_data)
        if result.schedule is not None:
            review.schedule = result.schedule.describe()
            review.next_run = self._next_run_text(result, program.path.parent)
        if approval is not None:
            old = self._compile(program.path, approval.text).permissions or ()
            new = result.permissions
            review.permissions_added = [_permission(p) for p in new if p not in old]
            review.permissions_removed = [_permission(p) for p in old if p not in new]
        return review

    def approve(self, review: Review, approver: str) -> None:
        """批准审核过的那份文本（不再读一次文件：批准的就是人看到的）"""
        if not approver.strip():
            raise PlatformError("承認者の名前を書いてください")
        if not review.approvable:
            raise PlatformError("検査に通らないプログラムは承認できません")
        self.store.add_approval(review.program.name, review.sha256, review.text, approver.strip(), self._stamp())

    # ---------- 一覧 ----------

    def statuses(self) -> list[Status]:
        return [self.status(p) for p in self.store.programs()]

    def status(self, program: Program) -> Status:
        runs = self.store.runs(program.name, limit=1)
        last = runs[0] if runs else None
        if not program.path.is_file():
            return Status(program, "ファイルなし", "－", None, last)
        text = program.path.read_text(encoding="utf-8")
        approval = self.store.latest_approval(program.name)
        if approval is None:
            state = "未承認"
        elif approval.sha256 == fingerprint(text):
            state = "承認済み"
        else:
            state = "変更あり（承認待ち）"
        approved = f"{approval.approver}（{approval.approved_at[:16].replace('T', ' ')}）" if approval else None
        result = self._compile(program.path, text)
        if result.errors:
            return Status(program, state, "（検査エラー）", None, last, approved)
        if result.schedule is None:
            return Status(program, state, "手動", None, last, approved)
        return Status(program, state, result.schedule.describe(),
                      self._next_run_text(result, program.path.parent), last, approved)

    def history(self, name: str | None = None, limit: int = 50) -> list[Run]:
        if name is not None:
            self._program(name)
        return self.store.runs(name, limit)

    # ---------- 内部 ----------

    def _program(self, name: str) -> Program:
        program = self.store.program(name)
        if program is None:
            raise PlatformError(f"「{name}」は登録されていません（dandori-platform 一覧 で確かめてください）")
        return program

    @staticmethod
    def _compile(path: Path, text: str):
        return api.compile_source(Source(text, path.name), base_dir=path.parent)

    def _next_run_text(self, result, base_dir: Path) -> str:
        try:
            return _when(api.next_run(result, self.now(), base_dir))
        except api.ScheduleError as e:
            return f"計算できません（{e}）"


def _permission(entry) -> str:
    kind, path, folder = entry
    return f"{kind}：「{path}」{'フォルダ' if folder else ''}"


def _diff(before: str, after: str, name: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), f"{name}（前回の承認）",
                                 f"{name}（今回）", lineterm="")
    return "\n".join(lines)


def _when(moment: datetime) -> str:
    weekday = "月火水木金土日"[moment.weekday()]
    return f"{moment.year}年{moment.month}月{moment.day}日（{weekday}）{moment:%H:%M}"
