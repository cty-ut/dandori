"""事务：全部成功才写入（语法设计 11.4，设计原则 7）

执行过程中，写出的内容先暂存在这里。程序全部成功后，api 调用 commit() 一次性写入；
中途出错时什么都不写。试运行时只报告"将要做什么"，永远不写。
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
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


class Transaction:
    def __init__(self) -> None:
        self._pending: list[_Pending] = []

    def stage(self, path: Path, display_name: str, content: bytes, overwrite: bool, rows: int) -> None:
        # 新建的文件在编译后、执行前被别人创建了：现在就报错（此时还什么都没写）
        if not overwrite and path.exists():
            raise VerbError("E5008", file=display_name)
        self._pending.append(_Pending(path, display_name, content, overwrite, rows))

    def report(self, done: bool) -> list[str]:
        """「試し」时：将要做什么；执行完成后：做了什么"""
        lines = []
        for p in self._pending:
            if done:
                action = "上書きしました" if p.existed else "新規作成しました"
            else:
                action = "上書きします" if p.overwrite and p.path.exists() else "新規作成します"
            lines.append(f"「{p.display_name}」を{action}（{p.rows}行）")
        return lines

    def entries(self) -> list[dict]:
        """执行日志用：写出了哪些文件"""
        return [{"file": p.display_name, "rows": p.rows,
                 "action": "上書き" if p.existed else "新規作成"} for p in self._pending]

    def commit(self) -> None:
        """先把所有内容写进同一文件夹中的临时文件，全部写好后再逐个替换为正式文件。

        写临时文件时出错：删除临时文件，正式文件一个都不变。
        替换（os.replace）对单个文件是原子操作：不会出现只写了一半的文件。
        """
        temps: list[tuple[str, _Pending]] = []
        try:
            for p in self._pending:
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
