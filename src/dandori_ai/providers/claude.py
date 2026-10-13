"""Claude（Anthropic SDK）

- 说明书放在系统提示中，加 cache_control（一次运行中不变，后续请求读缓存）
- 自适应思考（claude-opus-5-5 的默认），output_config.effort 控制深度
- 拒绝回答时由服务端的 fallbacks: "default" 改用其他模型；仍被拒绝时报错
- assistant 的内容（含思考块）原样回传
- 工具定义使用 strict: true，保证参数符合 JSON Schema
"""

from __future__ import annotations

from dandori_ai.providers.base import AssistantTurn, ProviderError, ToolCall

# 支持服务端 fallbacks: "default" 的模型
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


class ClaudeProvider:
    name = "claude"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "high", client=None):
        self.model = model
        self.effort = effort
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        self.client = client

    def complete(self, system: str, messages: list[dict], tools) -> AssistantTurn:
        import anthropic
        request = dict(
            model=self.model,
            max_tokens=16000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": t.name, "description": t.description, "input_schema": t.parameters,
                    "strict": True} for t in tools],
            messages=[self._convert(m) for m in messages],
            output_config={"effort": self.effort},
        )
        if self.model in _FALLBACK_MODELS:
            request.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        try:
            response = self.client.beta.messages.create(**request)
        except anthropic.AuthenticationError:
            raise ProviderError("Claude の API キーが正しくありません（ANTHROPIC_API_KEY）") from None
        except anthropic.RateLimitError:
            raise ProviderError("Claude の利用上限に達しました。しばらくしてから試してください") from None
        except anthropic.APIConnectionError:
            raise ProviderError("Claude に接続できません。ネットワークを確認してください") from None
        except anthropic.APIStatusError as e:
            raise ProviderError(f"Claude の呼び出しに失敗しました（{e.status_code}）：{e.message}") from None

        if response.stop_reason == "refusal":
            raise ProviderError("Claude がこの依頼への回答を断りました。要望の書き方を変えてみてください")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
        usage = getattr(response, "usage", None)
        return AssistantTurn(text, calls, raw=response.content, model=getattr(response, "model", self.model), usage={
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
        })

    @staticmethod
    def _convert(message: dict) -> dict:
        if message["role"] == "user":
            return {"role": "user", "content": [{"type": "text", "text": message["text"]}]}
        if message["role"] == "assistant":
            return {"role": "assistant", "content": message["raw"]}      # 思考块等原样回传
        return {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]}
            for r in message["results"]]}
