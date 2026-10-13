"""OpenAI 与 DeepSeek（OpenAI SDK，Chat Completions）

DeepSeek 的 API 与 OpenAI 兼容：同一个类，只是地址、密钥、模型名不同。
"""

from __future__ import annotations

import json
import os

from dandori_ai.providers.base import AssistantTurn, ProviderError, ToolCall


class OpenAICompatibleProvider:
    def __init__(self, model: str, api_key_env: str, name: str, base_url: str | None = None, client=None):
        self.model = model
        self.name = name
        if client is None:
            api_key = os.environ.get(api_key_env)
            if not api_key:
                raise ProviderError(f"環境変数 {api_key_env} に API キーを設定してください")
            from openai import OpenAI
            client = OpenAI(api_key=api_key, base_url=base_url)
        self.client = client

    def complete(self, system: str, messages: list[dict], tools) -> AssistantTurn:
        import openai
        converted = [{"role": "system", "content": system}]
        for m in messages:
            converted.extend(self._convert(m))
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=converted,
                tools=[{"type": "function", "function": {"name": t.name, "description": t.description,
                                                         "parameters": t.parameters}} for t in tools],
            )
        except openai.AuthenticationError:
            raise ProviderError(f"{self.name} の API キーが正しくありません") from None
        except openai.RateLimitError:
            raise ProviderError(f"{self.name} の利用上限に達しました。しばらくしてから試してください") from None
        except openai.APIConnectionError:
            raise ProviderError(f"{self.name} に接続できません。ネットワークを確認してください") from None
        except openai.APIStatusError as e:
            raise ProviderError(f"{self.name} の呼び出しに失敗しました（{e.status_code}）") from None

        message = response.choices[0].message
        calls = []
        for call in message.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"__invalid__": call.function.arguments}
            calls.append(ToolCall(call.id, call.function.name, arguments))
        usage = getattr(response, "usage", None)
        return AssistantTurn(message.content or "", calls, raw=None, model=getattr(response, "model", self.model), usage={
            "input_tokens": getattr(usage, "prompt_tokens", 0),
            "output_tokens": getattr(usage, "completion_tokens", 0),
        })

    @staticmethod
    def _convert(message: dict) -> list[dict]:
        if message["role"] == "user":
            return [{"role": "user", "content": message["text"]}]
        if message["role"] == "assistant":
            converted = {"role": "assistant", "content": message["text"] or None}
            if message["tool_calls"]:
                converted["tool_calls"] = [{
                    "id": c.id, "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments, ensure_ascii=False)},
                } for c in message["tool_calls"]]
            return [converted]
        return [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in message["results"]]
