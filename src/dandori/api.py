"""公开 API（架构设计 9.1）

外部工具（CLI、编辑器、AI 助手……）只通过这里使用语言。
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
    def rule_data(self) -> tuple[str, ...]:
        """程序使用的规则数据（祝日表等）及其来源和范围（「説明」）"""
        return self.program.rule_data


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


def run(source: Source, ctx: Context | None = None, registry: Registry | None = None
        ) -> list[Diagnostic]:
    """编译并执行。有编译错误时一行也不执行，返回所有错误

    写出的内容在程序全部成功后才写入（ctx.transaction.commit）；中途出错或试运行时什么都不写。
    """
    ctx = ctx or Context()
    registry = registry or ctx.registry or default_registry()
    ctx.registry = registry
    result = compile_source(source, registry, ctx.base_dir)
    if result.errors:
        return result.errors
    try:
        execute(result.program, ctx)
        if not ctx.dry_run:
            ctx.transaction.commit()
    except ExecutionError as e:
        return [e.diagnostic]
    except VerbError as e:      # 写入文件时失败
        return [Diagnostic.make(e.code, Span(0, 0), **e.params)]
    return []
