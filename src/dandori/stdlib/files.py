"""文件的读取与写出：読み込む、書き出す、上書きする（语法设计 8.3、11.2）

「読み込む」的结果类型取决于文件的内容（有哪些列、每列是什么类型），
所以它通过 infer 在编译时就读取文件。执行时再读一次，由解释器比对两次的结果。

写出的内容不会马上写进文件，而是先交给事务（ctx.transaction）暂存，
程序全部成功后才一次性写入（语法设计 11.4）。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from dandori.connectors import ConnectorError, excel
from dandori.connectors.csv import read_csv, write_csv
from dandori.frontend import ast
from dandori.frontend.conjugation import Conjugation
from dandori.plugin.registry import Effect, InferContext, Param, Registry, VerbError, VerbSpec
from dandori.runtime import values
from dandori.table.interface import Table
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


READ_SUFFIXES = (".csv", ".xlsx", ".xlsm")
WRITE_SUFFIXES = (".csv", ".xlsx")             # .xlsm は書き出さない（マクロを壊さないため）
_ALLOWED = {READ_SUFFIXES: "読み込めるのは CSV と Excel（.xlsx、.xlsm）",
            WRITE_SUFFIXES: "書き出せるのは CSV と Excel（.xlsx）"}


def resolve(file_name: str, base_dir: Path, suffixes: tuple[str, ...] = (".csv",)) -> Path:
    """文件名 → 路径。只允许程序所在的文件夹（及其子文件夹）以内（语法设计 11.7）

    suffixes：允许的扩展名。读取可以是 CSV 和 Excel，写出现在只有 CSV（语法设计 19）
    """
    pure = outside_folder(file_name)
    if pure is None:
        raise VerbError("E3038", file=file_name)
    suffix = pure.suffix.lower()
    if suffix == ".xls":
        raise VerbError("E3070", file=file_name)
    if suffix not in suffixes:
        raise VerbError("E3027", suffix=pure.suffix or file_name, allowed=_ALLOWED.get(suffixes, "CSV のみ"))
    return base_dir / pure


def describe_file(file_name: str, sheet: str | None) -> str:
    """「説明」等中显示的文件：「明細.csv」、「請求一覧.xlsx」の「10月」シート"""
    return f"「{file_name}」" + (f"の「{sheet}」シート" if sheet else "")


def _literal_path(args: dict[str, ast.Expr], verb: str) -> ast.String:
    path = args.get("path")
    if not isinstance(path, ast.String):
        raise VerbError("E3024", verb=verb)
    return path


# ---------- 読み込む ----------

def load_table(file_name: str, base_dir: Path, sheet: str | None = None) -> tuple[Table, str | None]:
    """读取 CSV 或 Excel。返回 (表, 给「説明」的补充说明)。补充说明：Excel 的表名（没有指定时）、表头的行"""
    path = resolve(file_name, base_dir, READ_SUFFIXES)
    try:
        if path.suffix.lower() in excel.SUFFIXES:
            result = excel.read_excel(path, file_name, sheet)
            notes = []
            if sheet is None:
                notes.append(f"「{result.sheet}」シート")
            if result.header_row != 1:
                notes.append(f"見出しは{result.header_row}行目")
            return result.table, "、".join(notes) or None
        if sheet is not None:
            raise VerbError("E3075", file=file_name)
        return read_csv(path, file_name), None
    except ConnectorError as e:
        raise VerbError(e.code, **e.params) from None


def infer_yomikomu(args: dict[str, ast.Expr], types: dict, ctx: InferContext):
    """编译时：读取文件，确定列定义"""
    path = _literal_path(args, "読み込む")
    table, note = load_table(path.value, ctx.base_dir, path.sheet)
    if note:
        ctx.notes[(path.value, path.sheet)] = note
    return table.type


def yomikomu(ctx, path):
    """「明細.csv」を読み込む"""
    check_permission(ctx, "読み込み", path)
    sheet = getattr(path, "sheet", None)
    try:
        table, _ = load_table(str(path), ctx.base_dir, sheet)
        ctx.record_read(str(path), resolve(path, ctx.base_dir, READ_SUFFIXES), len(table))
        return table
    except VerbError as e:
        # 执行时读取失败（编译后文件被删除、被修改等）
        raise VerbError("E5005", file=str(path), reason=_reason(e)) from None


def _reason(error: VerbError) -> str:
    from dandori.errors import MESSAGES
    return MESSAGES[error.code].format(**error.params)


# ---------- 書き出す・上書きする ----------

def _is_excel(file_name: str) -> bool:
    return Path(file_name).suffix.lower() in excel.SUFFIXES


def _write_target(path: ast.String, base_dir: Path, ctx: InferContext, overwrite: bool) -> Path:
    """编译时：写出的文件。Excel 时检查表名，并给「説明」补充说明"""
    if path.sheet is not None and not _is_excel(path.value):
        raise VerbError("E3075", file=path.value)
    target = resolve(path.value, base_dir, WRITE_SUFFIXES)
    if _is_excel(path.value):
        sheet = path.sheet or excel.default_sheet(path.value)
        try:
            excel.check_sheet_name(sheet)
        except ConnectorError as e:
            raise VerbError(e.code, **e.params) from None
        notes = [] if path.sheet else [f"「{sheet}」シート"]
        if overwrite:
            notes.append("ファイル全体を置き換えます。ほかのシートや書式は残りません")
        if notes:
            ctx.notes[(path.value, path.sheet)] = "、".join(notes)
    return target


def check_kakidasu(args, types, ctx: InferContext):
    """编译时：新建的文件不能已经存在"""
    path = _literal_path(args, "書き出す")
    if _write_target(path, ctx.base_dir, ctx, False).exists():
        raise VerbError("E3039", file=path.value)


def check_uwagaki(args, types, ctx: InferContext):
    _write_target(_literal_path(args, "上書きする"), ctx.base_dir, ctx, True)


def _stage(ctx, table, path, overwrite: bool) -> None:
    values.check_no_fraction(table)                # 带零头的金額不能写出（语法设计 12.5）
    target = resolve(path, ctx.base_dir, WRITE_SUFFIXES)
    if _is_excel(path):
        sheet = getattr(path, "sheet", None) or excel.default_sheet(path)
        ctx.transaction.stage_sheet(target, str(path), sheet, table, overwrite)
    else:
        ctx.transaction.stage(target, str(path), write_csv(table), overwrite, len(table))


def kakidasu(ctx, table, path):
    """集計を「集計.csv」に書き出す（新建）"""
    check_permission(ctx, "書き出し", path)
    _stage(ctx, table, path, False)


def uwagaki(ctx, table, path):
    """「集計.csv」を集計で上書きする"""
    check_permission(ctx, "上書き", path)
    _stage(ctx, table, path, True)


def register(registry: Registry) -> None:
    registry.add_verb(VerbSpec(
        "std.読み込む", "読み込む", Conjugation.GODAN,
        params=(Param("を", "path", frozenset({Type.STRING})),),
        returns=None,                 # 实际的类型由 infer 决定
        impl=yomikomu, infer=infer_yomikomu,
        effect=Effect.READ, resource_param="path", explain="{file}を読み込みます", permission="読み込み",
    ))
    registry.add_verb(VerbSpec(
        "std.書き出す", "書き出す", Conjugation.GODAN,
        params=(Param("を", "table", frozenset({TableType})),
                Param("に", "path", frozenset({Type.STRING}))),
        returns=None, impl=kakidasu, check=check_kakidasu,
        effect=Effect.WRITE, resource_param="path", explain="{file}を新規作成します", permission="書き出し",
    ))
    registry.add_verb(VerbSpec(
        "std.上書きする", "上書きする", Conjugation.SURU,
        params=(Param("を", "path", frozenset({Type.STRING})),
                Param("で", "table", frozenset({TableType}))),
        returns=None, impl=uwagaki, check=check_uwagaki,
        effect=Effect.WRITE, resource_param="path", explain="{file}を上書きします", permission="上書き",
    ))
