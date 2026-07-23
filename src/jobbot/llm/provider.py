from __future__ import annotations

import os
from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self, base_url: str | None = None, model: str = "gpt-4o-mini", api_key: str | None = None
    ) -> None:
        self.base_url = base_url or os.getenv("JOBBOT_OPENAI_BASE_URL", "")
        self.model = model
        self.api_key = api_key or os.getenv("JOBBOT_OPENAI_API_KEY")

    def generate(self, prompt: str) -> str:
        if os.getenv("JOBBOT_ALLOW_REMOTE_LLM", "false").lower() != "true":
            raise RuntimeError(
                "Remote LLM use is disabled; set JOBBOT_ALLOW_REMOTE_LLM=true "
                "only after explicit approval"
            )
        if not self.api_key:
            raise RuntimeError("OpenAI-compatible API key is not configured")
        return f"[stubbed completion for {self.model}] {prompt[:80]}"


class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str | None = None, model: str = "llama3.2") -> None:
        self.base_url = base_url or os.getenv("JOBBOT_LLM_BASE_URL", "http://localhost:11434/v1")
        self.model = model

    def generate(self, prompt: str) -> str:
        return f"[stubbed ollama response for {self.model}] {prompt[:80]}"
