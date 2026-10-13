"""文件的读取与写出：読み込む、書き出す、上書きする（语法设计 8.3、11.2）

「読み込む」的结果类型取决于文件的内容（有哪些列、每列是什么类型），
所以它通过 infer 在编译时就读取文件。执行时再读一次，由解释器比对两次的结果。

写出的内容不会马上写进文件，而是先交给事务（ctx.transaction）暂存，
程序全部成功后才一次性写入（语法设计 11.4）。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from dandori.connectors import ConnectorError
from dandori.connectors.csv import read_csv, write_csv
from dandori.frontend import ast
from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import Effect, InferContext, Param, Registry, VerbError, VerbSpec
from dandori.runtime import values
from dandori.types import TableType, Type


def check_permission(ctx, kind: str, file_name: str) -> None:
    """执行时的第二道检查（语法设计 17.1）：程序写了权限声明时，只能访问声明范围以内的文件"""
    permissions = getattr(ctx, "permissions", None)
    if permissions is not None and not allows(permissions, kind, file_name):
        raise VerbError("E5015", file=file_name, kind=kind)


def normalize_path(name: str) -> str:
    return PurePosixPath(name.replace("\\", "/")).as_posix()


def allows(permissions, kind: str, file_name: str) -> bool:
    """permissions：[(种类, 路径, 是否文件夹)]。文件夹包含其中所有的文件和子文件夹"""
    path = normalize_path(file_name)
    return any(k == kind and (path == p or (folder and path.startswith(p.rstrip("/") + "/")))
               for k, p, folder in permissions)


def outside_folder(file_name: str) -> PurePosixPath | None:
    """程序所在的文件夹以外的路径 → None；以内 → 规范化的路径（语法设计 11.7）

    不论在哪种操作系统上都按 POSIX 的规则判断：在 Windows 上，「/tmp/a.csv」不算绝对路径，
    但与文件夹连接后会变成「C:\\tmp\\a.csv」，跑出程序的文件夹。
    """
    pure = PurePosixPath(file_name.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or ":" in file_name:
        return None
    return pure


def resolve(file_name: str, base_dir: Path) -> Path:
    """文件名 → 路径。只允许程序所在的文件夹（及其子文件夹）以内（语法设计 11.7）"""
    pure = outside_folder(file_name)
    if pure is None:
        raise VerbError("E3038", file=file_name)
    if pure.suffix.lower() != ".csv":
        raise VerbError("E3027", suffix=pure.suffix or file_name)
    return base_dir / pure


def _literal_path(args: dict[str, ast.Expr], verb: str) -> str:
    path = args.get("path")
    if not isinstance(path, ast.String):
        raise VerbError("E3024", verb=verb)
    return path.value


# ---------- 読み込む ----------

def _load(file_name: str, base_dir: Path):
    try:
        return read_csv(resolve(file_name, base_dir), file_name)
    except ConnectorError as e:
        raise VerbError(e.code, **e.params) from None


def infer_yomikomu(args: dict[str, ast.Expr], types: dict, ctx: InferContext):
    """编译时：读取文件，确定列定义"""
    return _load(_literal_path(args, "読み込む"), ctx.base_dir).type


def yomikomu(ctx, path):
    """「明細.csv」を読み込む"""
    check_permission(ctx, "読み込み", path)
    try:
        table = _load(path, ctx.base_dir)
        ctx.record_read(path, resolve(path, ctx.base_dir), len(table))
        return table
    except VerbError as e:
        # 执行时读取失败（编译后文件被删除、被修改等）
        raise VerbError("E5005", file=path, reason=_reason(e)) from None


def _reason(error: VerbError) -> str:
    from dandori.errors import MESSAGES
    return MESSAGES[error.code].format(**error.params)


# ---------- 書き出す・上書きする ----------

def check_kakidasu(args, types, ctx: InferContext):
    """编译时：新建的文件不能已经存在"""
    file_name = _literal_path(args, "書き出す")
    if resolve(file_name, ctx.base_dir).exists():
        raise VerbError("E3039", file=file_name)


def check_uwagaki(args, types, ctx: InferContext):
    resolve(_literal_path(args, "上書きする"), ctx.base_dir)


def kakidasu(ctx, table, path):
    """集計を「集計.csv」に書き出す（新建）"""
    check_permission(ctx, "書き出し", path)
    values.check_no_fraction(table)                # 带零头的金額不能写出（语法设计 12.5）
    ctx.transaction.stage(resolve(path, ctx.base_dir), path, write_csv(table), False, len(table))


def uwagaki(ctx, table, path):
    """「集計.csv」を集計で上書きする"""
    check_permission(ctx, "上書き", path)
    values.check_no_fraction(table)
    ctx.transaction.stage(resolve(path, ctx.base_dir), path, write_csv(table), True, len(table))


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec(
        "std.読み込む", "読み込む", Conjugation.GODAN,
        params=(Param("を", "path", frozenset({Type.STRING})),),
        returns=None,                 # 实际的类型由 infer 决定
        impl=yomikomu, infer=infer_yomikomu,
        effect=Effect.READ, resource_param="path", explain="「{path}」を読み込みます", permission="読み込み",
    ))
    registry.add_verb(VerbSpec(
        "std.書き出す", "書き出す", Conjugation.GODAN,
        params=(Param("を", "table", frozenset({TableType})),
                Param("に", "path", frozenset({Type.STRING}))),
        returns=None, impl=kakidasu, check=check_kakidasu,
        effect=Effect.WRITE, resource_param="path", explain="「{path}」を新規作成します", permission="書き出し",
    ))
    registry.add_verb(VerbSpec(
        "std.上書きする", "上書きする", Conjugation.SURU,
        params=(Param("を", "path", frozenset({Type.STRING})),
                Param("で", "table", frozenset({TableType}))),
        returns=None, impl=uwagaki, check=check_uwagaki,
        effect=Effect.WRITE, resource_param="path", explain="「{path}」を上書きします", permission="上書き",
    ))
