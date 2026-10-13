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
class Name:
    """变量名：税率"""
    name: str        # 正規化后的名字（ＩＤ 和 ID 相同）
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


@dataclass(frozen=True)
class PreviousResult:
    """上一步的结果被自动填入空着的参数槽。由意味解析插入，構文解析不会产生"""
    span: Span


@dataclass(frozen=True)
class ErrorExpr:
    """不认识的词所在的位置。错误已经在断词时报过，后续阶段遇到它一律不再报错"""
    span: Span


Expr = Number | String | Sore | Name | ListExpr | BinaryOp | Negate | PreviousResult | ErrorExpr


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
class NameRef:
    name: str
    span: Span


@dataclass(frozen=True)
class Sentence:
    """手順文：用て形串起来的若干个手順，以「。」结尾

    句末可以给结果起名：100と200を足して、合計とする。
    """
    steps: tuple[Step, ...]
    span: Span
    bind: NameRef | None = None


@dataclass(frozen=True)
class Assignment:
    """代入文：税率は0.1とする。"""
    target: NameRef
    value: Expr
    span: Span


@dataclass(frozen=True)
class Program:
    sentences: tuple[Sentence | Assignment, ...]
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
        case Sentence(steps=steps, bind=None):
            return "文", list(steps)
        case Sentence(steps=steps, bind=bind):
            return f"文 → {bind.name}とする", list(steps)
        case Assignment(target=target, value=value):
            return f"代入 {target.name}", [value]
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
        case Name(name=name):
            return f"名前 {name}", []
        case PreviousResult():
            return "（前の結果）", []
        case ErrorExpr():
            return "（不明）", []
    raise TypeError(f"未知のノード：{node!r}")
