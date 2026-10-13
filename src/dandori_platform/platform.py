"""平台的核心（平台设计.md 第 5、6 节）。命令行（cli.py）只负责显示和确认

只通过公开 API（dandori.api）使用语言。
审核用的信息都来自编译器，不来自作者或 AI 的自述。

批准的对象 ＝ 程序全文 ＋ 规则文件（名義対応.csv 等，平台设计 5.2）。
每天变化的数据文件（入金明細.csv 等）不在批准的范围内。
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from dandori import api
from dandori.api import JST, Context, Source
from dandori_platform.store import Approval, Program, Run, Store

LATE = timedelta(minutes=5)          # 预定时间之后这么久才开始：「遅れて実行」
MAX_MISSED = 100_000                 # 计算错过的次数时的上限（毎日 × 几百年）


class PlatformError(Exception):
    """给用户看的错误（日文）"""


def fingerprint(data: str | bytes) -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


@dataclass
class Snapshot:
    """某一时刻的程序和规则文件"""
    text: str
    sha256: str
    rule_files: dict                   # 路径 → {"sha256", "text"}；文件不存在时 sha256 为 None
    result: api.CompileResult


@dataclass
class Review:
    """批准前给人看的内容"""
    program: Program
    snapshot: Snapshot                 # 审核的那份内容：批准的就是它
    errors: list[str]                  # 有错误时不能批准
    changes: list[str] | None = None   # 与上次批准相比变了的东西；第一次批准时为 None
    files: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    schedule: str | None = None
    next_run: str | None = None
    rule_data: list[str] = field(default_factory=list)
    diff: str | None = None            # 程序的差异
    rule_diffs: dict = field(default_factory=dict)    # 规则文件 → 差异
    permissions_added: list[str] = field(default_factory=list)
    permissions_removed: list[str] = field(default_factory=list)

    @property
    def approvable(self) -> bool:
        return not self.errors

    @property
    def already_approved(self) -> bool:
        return self.changes == []


@dataclass
class Status:
    """一覧的一行"""
    program: Program
    state: str                         # 承認済み / 未承認 / 変更あり（承認待ち） / ファイルなし
    schedule: str
    next_run: str | None
    last: Run | None
    approval: str | None = None        # 「田中（2026-10-14 09:00）」
    changes: list[str] = field(default_factory=list)


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
        snapshot = self._snapshot(program)
        result = snapshot.result
        review = Review(program, snapshot, [e.message for e in result.errors])
        approval = self.store.latest_approval(name)
        if approval is not None:
            review.changes = _changes(snapshot, approval)
            review.diff = _diff(approval.text, snapshot.text, program.path.name)
            for path in sorted(set(approval.rule_files) | set(snapshot.rule_files)):
                before = approval.rule_files.get(path, {}).get("text") or ""
                after = snapshot.rule_files.get(path, {}).get("text") or ""
                if before != after:
                    review.rule_diffs[path] = _diff(before, after, path)
        for path, entry in snapshot.rule_files.items():
            if entry["sha256"] is None:
                review.errors.append(f"規則のファイル「{path}」がありません")
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
        """批准审核过的那份内容（不再读一次文件：批准的就是人看到的）"""
        if not approver.strip():
            raise PlatformError("承認者の名前を書いてください")
        if not review.approvable:
            raise PlatformError("検査に通らないプログラムは承認できません")
        snapshot = review.snapshot
        self.store.add_approval(review.program.name, snapshot.sha256, snapshot.text, snapshot.rule_files,
                                approver.strip(), self._stamp())

    # ---------- 一覧 ----------

    def statuses(self) -> list[Status]:
        return [self.status(p) for p in self.store.programs()]

    def status(self, program: Program) -> Status:
        runs = self.store.runs(program.name, limit=1)
        last = runs[0] if runs else None
        if not program.path.is_file():
            return Status(program, "ファイルなし", "－", None, last)
        snapshot = self._snapshot(program)
        approval = self.store.latest_approval(program.name)
        changes = _changes(snapshot, approval) if approval else []
        if approval is None:
            state = "未承認"
        elif changes:
            state = "変更あり（承認待ち）"
        else:
            state = "承認済み"
        approved = f"{approval.approver}（{approval.approved_at[:16].replace('T', ' ')}）" if approval else None
        result = snapshot.result
        if result.errors:
            schedule, following = "（検査エラー）", None
        elif result.schedule is None:
            schedule, following = "手動", None
        else:
            schedule, following = result.schedule.describe(), self._next_run_text(result, program.path.parent)
        return Status(program, state, schedule, following, last, approved, changes)

    def history(self, name: str | None = None, limit: int = 50) -> list[Run]:
        if name is not None:
            self._program(name)
        return self.store.runs(name, limit)

    # ---------- 実行（平台设计 6） ----------

    def run(self, name: str, slot: datetime | None = None, missed: int = 0) -> Run | None:
        """运行一次。slot 是预定时间（手动运行时为 None）。

        同一个预定时间已经处理过时返回 None。没有批准、或批准后被改过时不运行，记为「承認待ち」。
        任何错误都记入履历，不抛出：一个程序的失败不影响其他程序。
        """
        program = self._program(name)
        now = self.now()
        late = slot is not None and now - slot >= LATE
        snapshot = self._snapshot(program) if program.path.is_file() else None
        run_id = self.store.start_run(name, slot.isoformat(timespec="seconds") if slot else None,
                                      now.isoformat(timespec="seconds"), snapshot and snapshot.sha256, late, missed)
        if run_id is None:
            return None
        try:
            self._execute(program, snapshot, run_id, (slot or now).astimezone(JST).date())
        except Exception as e:                      # noqa: BLE001  平台本身的问题也记入履历
            self.store.finish_run(run_id, "エラー", self._stamp(), f"基盤の内部エラー：{e}")
        return self.store.run(run_id)

    def _execute(self, program: Program, snapshot: Snapshot | None, run_id: int, today) -> None:
        if snapshot is None:
            self.store.finish_run(run_id, "エラー", self._stamp(), f"「{program.path}」がありません")
            return
        approval = self.store.latest_approval(program.name)
        if approval is None:
            self.store.finish_run(run_id, "承認待ち", self._stamp(), "まだ承認されていません")
            return
        if changes := _changes(snapshot, approval):
            self.store.finish_run(run_id, "承認待ち", self._stamp(),
                                  f"承認の後に変更されています：{'、'.join(changes)}")
            return

        output: list[str] = []
        ctx = Context(output=output.append, base_dir=program.path.parent, today=today)
        # 运行读取时的那份文本（与批准的完全相同），不再读一次文件
        outcome = api.run_with_log(Source(snapshot.text, program.path.name), ctx,
                                   log_dir=program.path.parent / "実行記録")
        log_path = outcome.log_path
        if output and log_path is not None:
            # 「表示する」的内容：无人值守时没人看屏幕，保存在日志旁边（与数据在同一个文件夹）
            log_path.with_name(f"{log_path.stem}_表示.txt").write_text("\n".join(output) + "\n", encoding="utf-8")
        if outcome.errors:
            self.store.finish_run(run_id, "エラー", self._stamp(), outcome.errors[0].message,
                                  str(log_path) if log_path else None)
        else:
            self.store.finish_run(run_id, "正常終了", self._stamp(), None, str(log_path) if log_path else None)

    # ---------- 調度（平台设计 6） ----------

    def due(self, program: Program) -> tuple[datetime, int] | None:
        """已经到了的预定时间（最近的一个）和错过的次数。没有时为 None

        予定以**批准的版本**为准；基准时间 ＝ 最后一个预定时间与最后一次批准中较晚的：
        批准之前的时间点不自动运行。
        """
        approval = self.store.latest_approval(program.name)
        if approval is None:
            return None
        result = self._compile(program.path, approval.text)
        if result.schedule is None:
            return None
        base_dir = program.path.parent
        baseline = datetime.fromisoformat(approval.approved_at)
        if last := self.store.last_slot(program.name):
            baseline = max(baseline, datetime.fromisoformat(last))
        now = self.now()
        slot = api.next_run(result, baseline, base_dir)
        if slot > now:
            return None
        missed = 0
        while missed < MAX_MISSED:
            following = api.next_run(result, slot, base_dir)
            if following > now:
                break
            slot, missed = following, missed + 1
        return slot, missed

    def tick(self) -> list[Run]:
        """检查一次：运行所有到了时间的程序"""
        done = []
        for program in self.store.programs():
            try:
                due = self.due(program)
            except Exception:                       # noqa: BLE001  算不出时间（祝日表范围外等）：一覧中显示
                continue
            if due is not None and (run := self.run(program.name, *due)) is not None:
                done.append(run)
        return done

    def interrupt_running(self) -> int:
        return self.store.interrupt_running(self._stamp())

    # ---------- 内部 ----------

    def _program(self, name: str) -> Program:
        program = self.store.program(name)
        if program is None:
            raise PlatformError(f"「{name}」は登録されていません（dandori-platform 一覧 で確かめてください）")
        return program

    @staticmethod
    def _compile(path: Path, text: str):
        return api.compile_source(Source(text, path.name), base_dir=path.parent)

    def _snapshot(self, program: Program) -> Snapshot:
        text = program.path.read_text(encoding="utf-8")
        result = self._compile(program.path, text)
        rule_files = {}
        for path in result.rule_files:
            file = program.path.parent / path
            if file.is_file():
                data = file.read_bytes()
                rule_files[path] = {"sha256": fingerprint(data), "text": _decode(data)}
            else:
                rule_files[path] = {"sha256": None, "text": None}
        return Snapshot(text, fingerprint(text), rule_files, result)

    def _next_run_text(self, result, base_dir: Path) -> str:
        try:
            return _when(api.next_run(result, self.now(), base_dir))
        except api.ScheduleError as e:
            return f"計算できません（{e}）"


def _changes(snapshot: Snapshot, approval: Approval) -> list[str]:
    """批准之后变了的东西：「プログラム」「「名義対応.csv」」"""
    changes = ["プログラム"] if snapshot.sha256 != approval.sha256 else []
    for path in sorted(set(approval.rule_files) | set(snapshot.rule_files)):
        before = approval.rule_files.get(path, {}).get("sha256")
        after = snapshot.rule_files.get(path, {}).get("sha256")
        if before != after:
            changes.append(f"「{path}」")
    return changes


def _decode(data: bytes) -> str:
    """规则文件的文本（显示差异用）：UTF-8 或 Shift_JIS"""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp932", errors="replace")


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
