"""模型提供者的统一接口

消息使用与模型无关的形式：
    {"role": "user", "text": "..."}
    {"role": "assistant", "text": "...", "tool_calls": [ToolCall...], "raw": 提供者自己的形式}
    {"role": "tool_results", "results": [{"id": ..., "content": ..., "is_error": ...}]}
每个提供者负责转换成自己的 API 形式。assistant 的 raw 原样回传（Claude 的思考块必须原样回传）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


class ProviderError(Exception):
    """模型调用失败（密钥、网络、拒绝回答等）。信息是给用户看的日文"""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class AssistantTurn:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: object = None
    usage: dict = field(default_factory=dict)
