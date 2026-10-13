"""常驻进程的文件锁：一个工作文件夹只能有一个（平台设计 6）

进程结束时（包括异常终止）操作系统自动释放锁，不会留下"锁住了却没有进程"的状态。
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    pass


@contextmanager
def single_instance(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        try:
            _lock(handle)
        except OSError:
            raise AlreadyRunning from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()


def _lock(handle) -> None:
    try:
        import fcntl
    except ImportError:                   # Windows
        import msvcrt
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
