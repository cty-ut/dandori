"""值的类型（语法设计 3.5）

阶段 1 只有数和文字列，以及由「と」连起来的并列。
"""

from enum import Enum


class Type(Enum):
    NUMBER = "数"
    STRING = "文字列"
    NUMBER_LIST = "数の並び"   # 100と200
    LIST = "並び"              # 含有非数的并列，如 1と「あ」
