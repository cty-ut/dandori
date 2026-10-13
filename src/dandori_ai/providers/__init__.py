"""模型提供者：Claude、OpenAI、DeepSeek（AI助手设计.md 第 6 节）"""

from __future__ import annotations

import os


def make_provider(name: str, model: str | None = None):
    name = name.lower()
    if name == "claude":
        from dandori_ai.providers.claude import ClaudeProvider
        return ClaudeProvider(model=model or "claude-opus-5-5")
    if name == "openai":
        from dandori_ai.providers.openai_compat import OpenAICompatibleProvider
        model = model or os.environ.get("DANDORI_AI_MODEL")
        if not model:
            from dandori_ai.providers.base import ProviderError
            raise ProviderError("OpenAI のモデルを --モデル か環境変数 DANDORI_AI_MODEL で指定してください")
        return OpenAICompatibleProvider(model=model, api_key_env="OPENAI_API_KEY", name="openai")
    if name == "deepseek":
        from dandori_ai.providers.openai_compat import OpenAICompatibleProvider
        return OpenAICompatibleProvider(model=model or "deepseek-chat", base_url="https://api.deepseek.com",
                                        api_key_env="DEEPSEEK_API_KEY", name="deepseek")
    from dandori_ai.providers.base import ProviderError
    raise ProviderError(f"「{name}」という提供元はありません（claude、openai、deepseek）")
