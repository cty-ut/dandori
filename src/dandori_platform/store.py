"""平台的数据（平台设计.md 第 4 节）：工作文件夹下的 .dandori/platform.db（SQLite）

时间都以 JST 的 ISO 格式字符串保存。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS programs (
    name          TEXT PRIMARY KEY,
    path          TEXT NOT NULL UNIQUE,
    registered_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals (
    id          INTEGER PRIMARY KEY,
    program     TEXT NOT NULL REFERENCES programs(name),
    sha256      TEXT NOT NULL,
    text        TEXT NOT NULL,          -- 程序全文：下次批准时显示差异
    rule_files  TEXT NOT NULL DEFAULT '{}',  -- 规则文件：JSON {路径: {"sha256", "text"}}
    approver    TEXT NOT NULL,
    approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    program     TEXT NOT NULL REFERENCES programs(name),
    slot        TEXT,                   -- 预定时间；手动运行时为 NULL
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,          -- 実行中 / 正常終了 / エラー / 承認待ち / 中断
    late        INTEGER NOT NULL DEFAULT 0,
    missed      INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    log_path    TEXT,
    sha256      TEXT,
    UNIQUE (program, slot)              -- 同一个预定时间最多运行一次（NULL 不受限制）
);
"""


@dataclass(frozen=True)
class Program:
    name: str
    path: Path
    registered_at: str


@dataclass(frozen=True)
class Approval:
    sha256: str
    text: str
    approver: str
    approved_at: str
    rule_files: dict          # 路径 → {"sha256", "text"}


@dataclass(frozen=True)
class Run:
    id: int
    program: str
    slot: str | None
    started_at: str
    finished_at: str | None
    status: str
    late: bool
    missed: int
    error: str | None
    log_path: str | None


class Store:
    def __init__(self, workspace: Path):
        folder = workspace / ".dandori"
        folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(folder / "platform.db", isolation_level=None)   # 自动提交
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        # 8-2 时创建的数据库没有 rule_files 列
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(approvals)")}
        if "rule_files" not in columns:
            self.db.execute("ALTER TABLE approvals ADD COLUMN rule_files TEXT NOT NULL DEFAULT '{}'")

    def close(self) -> None:
        self.db.close()

    # ---------- 程序 ----------

    def add_program(self, name: str, path: Path, now: str) -> None:
        self.db.execute("INSERT INTO programs VALUES (?, ?, ?)", (name, str(path), now))

    def program(self, name: str) -> Program | None:
        row = self.db.execute("SELECT name, path, registered_at FROM programs WHERE name = ?", (name,)).fetchone()
        return Program(row[0], Path(row[1]), row[2]) if row else None

    def program_by_path(self, path: Path) -> Program | None:
        row = self.db.execute("SELECT name, path, registered_at FROM programs WHERE path = ?", (str(path),)).fetchone()
        return Program(row[0], Path(row[1]), row[2]) if row else None

    def programs(self) -> list[Program]:
        rows = self.db.execute("SELECT name, path, registered_at FROM programs ORDER BY name").fetchall()
        return [Program(r[0], Path(r[1]), r[2]) for r in rows]

    # ---------- 承認 ----------

    def add_approval(self, program: str, sha256: str, text: str, rule_files: dict, approver: str, now: str) -> None:
        self.db.execute("INSERT INTO approvals (program, sha256, text, rule_files, approver, approved_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (program, sha256, text, json.dumps(rule_files, ensure_ascii=False), approver, now))

    def latest_approval(self, program: str) -> Approval | None:
        """有效的只有最后一次批准：改回以前批准过的版本，也要重新批准"""
        row = self.db.execute("SELECT sha256, text, approver, approved_at, rule_files FROM approvals "
                              "WHERE program = ? ORDER BY id DESC LIMIT 1", (program,)).fetchone()
        return Approval(*row[:4], json.loads(row[4])) if row else None

    # ---------- 履歴 ----------

    def start_run(self, program: str, slot: str | None, now: str, sha256: str | None,
                  late: bool = False, missed: int = 0) -> int | None:
        """插入一条"実行中"的记录。同一个预定时间已经有记录时返回 None（已经处理过）"""
        try:
            cursor = self.db.execute(
                "INSERT INTO runs (program, slot, started_at, status, late, missed, sha256) "
                "VALUES (?, ?, ?, '実行中', ?, ?, ?)", (program, slot, now, int(late), missed, sha256))
        except sqlite3.IntegrityError:
            return None
        return cursor.lastrowid

    def finish_run(self, run_id: int, status: str, now: str, error: str | None = None,
                   log_path: str | None = None) -> None:
        self.db.execute("UPDATE runs SET status = ?, finished_at = ?, error = ?, log_path = ? WHERE id = ?",
                        (status, now, error, log_path, run_id))

    def runs(self, program: str | None = None, limit: int = 50) -> list[Run]:
        where, args = ("WHERE program = ?", (program,)) if program else ("", ())
        rows = self.db.execute(
            "SELECT id, program, slot, started_at, finished_at, status, late, missed, error, log_path "
            f"FROM runs {where} ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [Run(*r[:6], bool(r[6]), *r[7:]) for r in rows]

    def run(self, run_id: int) -> Run:
        r = self.db.execute("SELECT id, program, slot, started_at, finished_at, status, late, missed, error, log_path "
                            "FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run(*r[:6], bool(r[6]), *r[7:])

    def last_slot(self, program: str) -> str | None:
        row = self.db.execute("SELECT MAX(slot) FROM runs WHERE program = ?", (program,)).fetchone()
        return row[0]

    def interrupt_running(self, now: str) -> int:
        """常驻进程启动时：残留的"実行中"（上次进程中途停止）改为"中断\""""
        cursor = self.db.execute("UPDATE runs SET status = '中断', finished_at = ? WHERE status = '実行中'", (now,))
        return cursor.rowcount
