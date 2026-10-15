"""事务：全部成功才写入（语法设计 11.4，设计原则 7）

执行过程中，写出的内容先暂存在这里。程序全部成功后，api 调用 commit() 一次性写入；
中途出错时什么都不写。试运行时只报告"将要做什么"，永远不写。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dandori.plugin.registry import VerbError


@dataclass
class _Pending:
    path: Path
    display_name: str
    content: bytes
    overwrite: bool        # True：上書きする；False：書き出す（新建）
    rows: int
    existed: bool = False  # 写入前文件是否已存在（用于报告"上書きしました"还是"新規作成しました"）
    # Excel：同一个文件的多张表，提交时合成一个工作簿（语法设计 19.3）。[(表名, 表)]
    sheets: list = field(default_factory=list)

    def lines(self) -> list[tuple[str, int]]:
        """报告用：(显示的名字, 行数)。Excel 每张表一行"""
        if not self.sheets:
            return [(f"「{self.display_name}」", self.rows)]
        return [(f"「{self.display_name}」の「{name}」シート", len(table)) for name, table in self.sheets]


class Transaction:
    def __init__(self) -> None:
        self._pending: list[_Pending] = []

    def stage(self, path: Path, display_name: str, content: bytes, overwrite: bool, rows: int) -> None:
        # 新建的文件在编译后、执行前被别人创建了：现在就报错（此时还什么都没写）
        if not overwrite and path.exists():
            raise VerbError("E5008", file=display_name)
        self._pending.append(_Pending(path, display_name, content, overwrite, rows))

    def stage_sheet(self, path: Path, display_name: str, sheet: str, table, overwrite: bool) -> None:
        """Excel 的一张表。同一个文件的表合在一起，提交时写成一个工作簿"""
        for p in self._pending:
            if p.path == path:
                # 编译时已经检查过（E3040、E3078），这里是最后一道防线
                if p.overwrite != overwrite:
                    raise VerbError("E3078", file=display_name)
                if any(name == sheet for name, _ in p.sheets):
                    raise VerbError("E3040", file=f"{display_name}」の「{sheet}")
                p.sheets.append((sheet, table))
                return
        if not overwrite and path.exists():
            raise VerbError("E5008", file=display_name)
        self._pending.append(_Pending(path, display_name, b"", overwrite, 0, sheets=[(sheet, table)]))

    def report(self, done: bool) -> list[str]:
        """「試し」时：将要做什么；执行完成后：做了什么"""
        lines = []
        for p in self._pending:
            if done:
                action = "上書きしました" if p.existed else "新規作成しました"
            else:
                action = "上書きします" if p.overwrite and p.path.exists() else "新規作成します"
            lines += [f"{name}を{action}（{rows}行）" for name, rows in p.lines()]
        return lines

    def entries(self) -> list[dict]:
        """执行日志用：写出了哪些文件"""
        entries = []
        for p in self._pending:
            action = "上書き" if p.existed else "新規作成"
            if p.sheets:
                entries += [{"file": p.display_name, "sheet": name, "rows": len(table), "action": action}
                            for name, table in p.sheets]
            else:
                entries.append({"file": p.display_name, "rows": p.rows, "action": action})
        return entries

    def commit(self) -> None:
        """先把所有内容写进同一文件夹中的临时文件，全部写好后再逐个替换为正式文件。

        写临时文件时出错：删除临时文件，正式文件一个都不变。
        替换（os.replace）对单个文件是原子操作：不会出现只写了一半的文件。
        """
        temps: list[tuple[str, _Pending]] = []
        try:
            for p in self._pending:
                if p.sheets:
                    from dandori.connectors.excel import write_excel
                    p.content = write_excel(p.sheets)
                p.path.parent.mkdir(parents=True, exist_ok=True)
                fd, temp = tempfile.mkstemp(dir=p.path.parent, prefix=".dandori-", suffix=".tmp")
                with os.fdopen(fd, "wb") as f:
                    f.write(p.content)
                temps.append((temp, p))
        except OSError as e:
            for temp, _ in temps:
                Path(temp).unlink(missing_ok=True)
            failed = self._pending[len(temps)]
            raise VerbError("E5009", file=failed.display_name, reason=e.strerror or str(e)) from None

        for temp, p in temps:
            p.existed = p.path.exists()
            os.replace(temp, p.path)
