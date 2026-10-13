"""文字显示的小工具"""

import unicodedata


def display_width(text: str) -> int:
    """终端里的显示宽度：全角字符占 2 格"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str, width: int, right: bool = False) -> str:
    """按显示宽度补空格。right=True 时右对齐（用于数）"""
    padding = " " * (width - display_width(text))
    return padding + text if right else text + padding
