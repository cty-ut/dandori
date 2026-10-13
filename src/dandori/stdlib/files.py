"""文件的读取：読み込む（语法设计 8.3、8.5）

「読み込む」的结果类型取决于文件的内容（有哪些列、每列是什么类型），
所以它通过 infer 在编译时就读取文件。执行时再读一次，由解释器比对两次的结果。
"""

from __future__ import annotations

from pathlib import Path

from dandori.connectors import ConnectorError
from dandori.connectors.csv import read_csv
from dandori.frontend import ast
from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import InferContext, Param, Registry, VerbError, VerbSpec
from dandori.types import Type


def _load(file_name: str, base_dir: Path):
    suffix = Path(file_name).suffix.lower()
    if suffix != ".csv":
        raise VerbError("E3027", suffix=suffix or file_name)
    try:
        return read_csv(base_dir / file_name, file_name)
    except ConnectorError as e:
        raise VerbError(e.code, **e.params) from None


def infer_yomikomu(args: dict[str, ast.Expr], types: dict, ctx: InferContext):
    """编译时：读取文件，确定列定义"""
    path = args.get("path")
    if not isinstance(path, ast.String):
        raise VerbError("E3024")
    return _load(path.value, ctx.base_dir).type


def yomikomu(ctx, path):
    """「明細.csv」を読み込む"""
    try:
        return _load(path, ctx.base_dir)
    except VerbError as e:
        # 执行时读取失败（编译后文件被删除、被修改等）
        raise VerbError("E5005", file=path, reason=_reason(e)) from None


def _reason(error: VerbError) -> str:
    from dandori.errors import MESSAGES
    return MESSAGES[error.code].format(**error.params)


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec(
        "std.読み込む", "読み込む", Conjugation.GODAN,
        params=(Param("を", "path", frozenset({Type.STRING})),),
        returns=None,                 # 实际的类型由 infer 决定
        impl=yomikomu, infer=infer_yomikomu,
    ))
