"""假模型：按顺序返回预先写好的回答。测试用（不需要密钥、不花钱）"""

from __future__ import annotations

from dandori_ai.providers.base import AssistantTurn, ProviderError, ToolCall


class FakeProvider:
    name = "fake"

    def __init__(self, script):
        """script：AssistantTurn，或者 messages → AssistantTurn 的函数（可以根据工具的返回值决定下一步）"""
        self.script = list(script)
        self.requests: list[list[dict]] = []

    def complete(self, system: str, messages: list[dict], tools) -> AssistantTurn:
        self.requests.append(list(messages))
        if not self.script:
            raise ProviderError("假モデルの回答が尽きました（テストの台本が足りません）")
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def call(name: str, n: int = 1, **arguments) -> ToolCall:
    return ToolCall(f"call_{n}", name, arguments)


def submit(program: str, summary: str = "", questions=(), n: int = 1) -> AssistantTurn:
    return AssistantTurn("", [call("submit_program", n, program=program, summary=summary,
                                   open_questions=list(questions))])
