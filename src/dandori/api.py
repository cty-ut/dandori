"""公开 API（架构设计 9.1）

外部工具（CLI、编辑器、AI 助手……）只通过这里使用语言。
Source（源码）和 Context（执行上下文）也从这里引用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dandori.errors import Diagnostic
from dandori.frontend import ast
from dandori.frontend.analyzer import BoundProgram, Resource, analyze
from dandori.frontend.lexer import tokenize
from dandori.frontend.lexicon import Lexicon
from dandori.frontend.parser import parse
from dandori.frontend.source import Source, Span
from dandori.plugin.registry import Registry, VerbError, default_registry
from dandori.runtime.interpreter import Context, ExecutionError, execute


@dataclass
class CompileResult:
    tree: ast.Program
    program: BoundProgram
    errors: list[Diagnostic]   # 按出现位置排序

    @property
    def resources(self) -> tuple[Resource, ...]:
        """程序会读取、写出哪些文件（「説明」）"""
        return self.program.resources

    @property
    def permissions(self):
        """权限声明：[(种类, 路径, 是否文件夹)]；没有写时为 None"""
        return self.program.permissions

    def unused_permissions(self) -> list[tuple]:
        """声明了却没有使用的权限（权限越小越安全）"""
        if self.permissions is None:
            return []
        from dandori.stdlib.files import allows
        return [entry for entry in self.permissions
                if not any(allows([entry], r.permission, r.path) for r in self.resources)]

    @property
    def rule_data(self) -> tuple[str, ...]:
        """程序使用的规则数据（祝日表等）及其来源和范围（「説明」）"""
        return self.program.rule_data

    @property
    def settings(self) -> dict[tuple[str, str], object]:
        """设定文：(名前空間, 設定) → 值。设定只能写在最外层"""
        from dandori.frontend.analyzer import BoundSetting
        return {(s.namespace, s.key): s.value for s in self.program.sentences
                if isinstance(s, BoundSetting) and s.value is not None}

    @property
    def schedule(self):
        """「実行の予定」（平台设计 3）；没有写、或有编译错误时为 None"""
        from dandori.stdlib.japan.schedule import parse_schedule
        text = self.settings.get(("実行", "予定"))
        if text is None or self.errors:
            return None
        return parse_schedule(text)


class ScheduleError(Exception):
    """无法计算下次运行时间（如祝日表的范围不够）。信息是日文"""


def next_run(result: CompileResult, after, base_dir: Path | None = None, registry: Registry | None = None):
    """after（datetime）之后的下一次运行时间（JST）。没有予定时为 None

    用到公司营业日时，读取「暦の会社休日」的文件（以 base_dir 为准）。
    """
    from dandori.errors import MESSAGES
    from dandori.stdlib.japan import _company_days
    schedule = result.schedule
    if schedule is None:
        return None
    registry = registry or default_registry()
    try:
        company = None
        if schedule.uses_company:
            _, days = _company_days(result.settings[("暦", "会社休日")], base_dir or Path.cwd())
            company = registry.calendar.with_company_holidays(days)
        return schedule.next_after(after, registry.calendar, company)
    except VerbError as e:
        raise ScheduleError(MESSAGES[e.code].format(**e.params)) from None


def compile_source(source: Source, registry: Registry | None = None,
                   base_dir: Path | None = None) -> CompileResult:
    """断词 → 構文解析 → 意味解析，收集所有阶段的错误

    base_dir：程序文件所在的目录，「読み込む」的相对路径以它为准（默认为当前目录）
    """
    registry = registry or default_registry()
    tokens, lex_errors = tokenize(source, Lexicon.from_registry(registry))
    tree, parse_errors = parse(tokens, source)
    program, analyze_errors = analyze(tree, registry, source, base_dir)
    errors = sorted(lex_errors + parse_errors + analyze_errors, key=lambda e: e.span.start)
    return CompileResult(tree, program, errors)


@dataclass
class RunResult:
    errors: list[Diagnostic]
    log_path: Path | None = None       # 写出的执行日志（语法设计 17.2）


def run(source: Source, ctx: Context | None = None, registry: Registry | None = None) -> list[Diagnostic]:
    """编译并执行。有编译错误时一行也不执行，返回所有错误

    写出的内容在程序全部成功后才写入（ctx.transaction.commit）；中途出错或试运行时什么都不写。
    """
    return run_with_log(source, ctx, registry).errors


def run_with_log(source: Source, ctx: Context | None = None, registry: Registry | None = None,
                 log_dir: Path | None = None) -> RunResult:
    """与 run 相同；给出 log_dir 时，执行后在那里写执行日志（试运行不写）"""
    import time
    from dandori.runtime.log import ExecutionLog

    ctx = ctx or Context()
    registry = registry or ctx.registry or default_registry()
    ctx.registry = registry
    log = ExecutionLog(source)
    started = time.monotonic()

    result = compile_source(source, registry, ctx.base_dir)
    errors, executed = result.errors, False
    if not errors:
        executed = True
        try:
            execute(result.program, ctx)
            if not ctx.dry_run:
                ctx.transaction.commit()
        except ExecutionError as e:
            errors = [e.diagnostic]
        except VerbError as e:      # 写入文件时失败
            errors = [Diagnostic.make(e.code, Span(0, 0), **e.params)]

    log_path = None
    if log_dir is not None and not ctx.dry_run:
        record = log.finish(ctx, result.rule_data, errors, time.monotonic() - started, executed)
        try:
            log_path = log.write(record, log_dir)
        except OSError:
            log_path = None             # 日志写入失败只给出警告，不影响执行结果（由调用方提示）
    return RunResult(errors, log_path)


# ---------- 给外部工具（编辑器、AI 助手等）用的信息（架构设计 9.1） ----------

def diagnostics(errors: list[Diagnostic], source: Source) -> list[dict]:
    """错误 → 机器可读的形式：错误码、行、列、信息"""
    result = []
    for error in errors:
        line, column = source.line_col(error.span.start)
        result.append({"code": error.code, "line": line, "column": column, "message": error.message,
                       "text": source.slice(error.span)})
    return result


def catalog(registry: Registry | None = None) -> dict:
    """语言提供的动词、设定、日付属性、规则数据。从注册表生成：语言增加了动词，这里自动就有"""
    from dandori.frontend.conjugation import Form, conjugate
    registry = registry or default_registry()
    verbs = []
    for spec in registry.verbs:
        forms = conjugate(spec.dictionary_form, spec.conjugation)
        verbs.append({
            "word": spec.dictionary_form,
            "te_form": forms[Form.TE],
            "params": [{
                "particle": p.particle,
                "kind": p.kind.value,
                "accepts": _accepts(p.accepts),
                "required": p.required,
            } for p in spec.params],
            "effect": spec.effect.value,
            "permission": spec.permission,
            "doc": _example(spec.impl.__doc__),
        })
    settings = [{"namespace": ns, "key": key, "kind": s.kind.name, "example": s.example or s.kind.value}
                for (ns, key), s in registry._settings.items()]
    properties = [{"name": name, "result": registry.date_property(name).result.value}
                  for name in registry.date_property_names]
    return {"verbs": verbs, "settings": settings, "date_properties": properties,
            "rule_data": dict(registry.rule_data)}


def _example(doc: str | None) -> str:
    """实现函数说明的第一行中的用例部分：去掉第一个「。」之后的内容，以及不含假名的括号注释（开发者的说明）"""
    import re
    if not doc:
        return ""
    line = doc.strip().splitlines()[0].split("。")[0]
    return re.sub(r"（[^（）ぁ-んァ-ヶ]*）", "", line).strip()


def _accepts(accepts) -> list[str] | None:
    from dandori.types import TableType
    if accepts is None:
        return None
    return sorted("表" if a is TableType else a.value for a in accepts)


def describe_csv(file_name: str, base_dir: Path, sample_rows: int = 0) -> dict:
    """CSV 的结构：列名、类型、行数、各列的空欄数。sample_rows > 0 时附上前几行（默认不附：不发送客户数据）

    空欄数是统计信息，不是数据本身；AI 要知道有空欄，才会想到问"空欄怎么处理"。
    """
    from dandori.connectors import ConnectorError
    from dandori.connectors.csv import read_csv
    from dandori.runtime.values import format_value
    from dandori.stdlib.files import resolve
    from dandori.table.interface import BLANK
    try:
        table = read_csv(resolve(file_name, base_dir), file_name)
    except (ConnectorError, VerbError) as e:
        from dandori.errors import MESSAGES
        return {"error": MESSAGES[e.code].format(**e.params)}
    info = {
        "file": file_name,
        "columns": [{"name": shown, "type": t.value, "blanks": sum(row[i] is BLANK for row in table.rows)}
                    for i, (shown, (_, t)) in enumerate(zip(table.display_names, table.type.columns))],
        "rows": len(table),
    }
    if sample_rows > 0:
        info["sample"] = [[format_value(v) for v in row] for row in table.rows[:sample_rows]]
    return info
