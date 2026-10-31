"""コンソール出力の文字コード対策

日本語版 Windows で出力をファイルにリダイレクトしたときや、タスク
スケジューラから動かしたとき、標準出力の文字コードが cp932 になる
ことがある。cp932 で表せない文字（「⚠」「𠮷」など）を表示しようと
すると UnicodeEncodeError で止まってしまうため、CLI の入口で
「表せない文字は ? に置き換える」設定に変える（Issue #1）。

データを書き出すファイルは対象外（そちらは UTF-8 で書いている）。
"""

from __future__ import annotations

import sys


def tolerate_unrepresentable_chars() -> None:
    """標準出力・標準エラーで、表せない文字があっても止まらないようにする。

    テスト用の差し替えなど、reconfigure を持たないストリームや、
    すでに閉じているストリームでは何もしない。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):
            pass
