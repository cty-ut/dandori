"""纠错循环（AI助手设计.md 第 5 节）：与模型无关

    要望 → AI（使用工具）→ 提出 → 编译检查 → 通过：结束
                                         → 错误：交还给 AI 修改 → …

最多 max_turns 轮。AI 只写文本，从不执行程序。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dandori_ai.reference import system_prompt
from dandori_ai.tools import TOOLS, Toolbox


class GenerationFailed(Exception):
    def __init__(self, message: str, last_errors=None):
        super().__init__(message)
        self.last_errors = last_errors or []


@dataclass
class Outcome:
    program: str
    summary: str
    open_questions: list[str]
    turns: int
    checks: int
    usage: dict = field(default_factory=dict)


REMINDER = "プログラムができたら submit_program で提出してください。"


def run_session(provider, toolbox: Toolbox, request: str, max_turns: int = 12, system: str | None = None
                ) -> Outcome:
    system = system or system_prompt()
    messages: list[dict] = [{"role": "user", "text": request}]
    usage: dict[str, int] = {}
    reminders = 0
    last_errors = []

    for turn in range(1, max_turns + 1):
        reply = provider.complete(system, messages, TOOLS)
        for key, value in reply.usage.items():
            usage[key] = usage.get(key, 0) + (value or 0)
        messages.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls, "raw": reply.raw})

        if not reply.tool_calls:
            if reminders >= 2:
                break
            reminders += 1
            messages.append({"role": "user", "text": REMINDER})
            continue

        results, submitted = [], None
        for call in reply.tool_calls:
            result = toolbox.execute(call.name, call.arguments)
            results.append({"id": call.id, "content": result.content, "is_error": result.is_error})
            if result.submitted:
                submitted = result.submitted
            elif result.is_error and call.name in ("check_program", "submit_program"):
                last_errors = toolbox.history[-1] if toolbox.history else []
        if submitted:
            return Outcome(submitted["program"], submitted["summary"], submitted["open_questions"],
                           turn, toolbox.checks, usage)
        messages.append({"role": "tool_results", "results": results})

    raise GenerationFailed(f"{max_turns}回のやり取りで、検査に通るプログラムを作れませんでした", last_errors)
