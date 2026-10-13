"""抽象语法树（语法设计 2.6）

構文解析的产物。每个节点都带 span，可以指回原文。
構文解析器只记录"长什么样"：每个手順里出现了哪些助词和值。
助词与参数槽的对应由意味解析负责。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from dandori.frontend.conjugation import Form
from dandori.frontend.source import Span


# ---------- 值（式） ----------

@dataclass(frozen=True)
class Number:
    value: int | Decimal
    span: Span


@dataclass(frozen=True)
class String:
    value: str
    span: Span


@dataclass(frozen=True)
class Sore:
    """指示語「それ」：上一步的结果"""
    span: Span


@dataclass(frozen=True)
class ListExpr:
    """用「と」连起来的值：100と200"""
    items: tuple[Expr, ...]
    span: Span


@dataclass(frozen=True)
class BinaryOp:
    op: str          # 标准写法：+ - * /
    left: Expr
    right: Expr
    span: Span


@dataclass(frozen=True)
class Negate:
    """负号：-5"""
    operand: Expr
    span: Span


Expr = Number | String | Sore | ListExpr | BinaryOp | Negate


# ---------- 句子结构 ----------

@dataclass(frozen=True)
class Arg:
    """引数句：值 + 助词，如「200を」"""
    particle: str
    value: Expr
    span: Span


@dataclass(frozen=True)
class Verb:
    verb_id: str
    lemma: str       # 辞书形
    form: Form       # 活用形
    span: Span


@dataclass(frozen=True)
class Step:
    """手順：若干个引数句 + 一个动词"""
    args: tuple[Arg, ...]
    verb: Verb
    span: Span


@dataclass(frozen=True)
class Sentence:
    """文：用て形串起来的若干个手順，以「。」结尾"""
    steps: tuple[Step, ...]
    span: Span


@dataclass(frozen=True)
class Program:
    sentences: tuple[Sentence, ...]
    span: Span


# ---------- 打印成树（调试用） ----------

def dump(node) -> str:
    """把语法树画出来：

    プログラム
    └─ 文
       ├─ 手順 足す（て形）
       │  └─ を
       │     └─ 列挙
       ...
    """
    lines: list[str] = []
    _dump(node, "", "", lines)
    return "\n".join(lines)


def _dump(node, first_prefix: str, rest_prefix: str, lines: list[str]) -> None:
    label, children = _describe(node)
    lines.append(first_prefix + label)
    for i, child in enumerate(children):
        last = i == len(children) - 1
        _dump(child,
              rest_prefix + ("└─ " if last else "├─ "),
              rest_prefix + ("   " if last else "│  "),
              lines)


def _describe(node) -> tuple[str, list]:
    match node:
        case Program(sentences=sentences):
            return "プログラム", list(sentences)
        case Sentence(steps=steps):
            return "文", list(steps)
        case Step(args=args, verb=verb):
            return f"手順 {verb.lemma}（{verb.form.value}）", list(args)
        case Arg(particle=particle, value=value):
            return particle, [value]
        case ListExpr(items=items):
            return "列挙", list(items)
        case BinaryOp(op=op, left=left, right=right):
            return {"+": "＋", "-": "－", "*": "×", "/": "÷"}[op], [left, right]
        case Negate(operand=operand):
            return "マイナス", [operand]
        case Number(value=value):
            return f"数 {value}", []
        case String(value=value):
            return f"文字列 「{value}」", []
        case Sore():
            return "それ", []
    raise TypeError(f"未知のノード：{node!r}")
