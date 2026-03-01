"""Thin wrapper around LLM API clients.

Supports OpenAI-compatible APIs. Decoupled from the rest of the system
so it can be swapped or mocked easily.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


class AIClient:
    """Generic LLM chat client."""

    def chat(self, messages: list[dict]) -> str:
        raise NotImplementedError


class OpenAIClient(AIClient):
    """OpenAI API client."""

    def __init__(self, api_key: str, model: str = "gpt-4", temperature: float = 0.3):
        import openai

        self._client = openai.OpenAI(api_key=api_key)
        self._model = model
        self._temperature = temperature

    def chat(self, messages: list[dict]) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=self._temperature,
            max_tokens=4096,
        )
        return response.choices[0].message.content or ""


class MockAIClient(AIClient):
    """Mock client for testing without an actual LLM API.

    Returns a minimal valid YAML test suite or analysis JSON.
    """

    def chat(self, messages: list[dict]) -> str:
        last_msg = messages[-1]["content"] if messages else ""

        if "analyze" in (messages[0].get("content", "") if messages else "").lower():
            return (
                '{"root_cause": "Mock analysis — unable to determine root cause without LLM", '
                '"category": "test_issue", "confidence": 0.3, '
                '"suggestion": "Configure a real AI provider for accurate analysis", '
                '"related_issues": []}'
            )

        return (
            "suite: ai_generated_mock\n"
            'description: "Mock-generated test suite (configure AI provider for real generation)"\n'
            "tags: [mock]\n"
            "setup:\n"
            '  - "CREATE TABLE t_mock (id INT PRIMARY KEY, val VARCHAR(32))"\n'
            '  - "INSERT INTO t_mock VALUES (1, \'hello\')"\n'
            "cases:\n"
            "  - id: mock_select\n"
            '    description: "Mock select test"\n'
            '    sql: "SELECT id, val FROM t_mock ORDER BY id"\n'
            "    expect:\n"
            "      type: rows\n"
            "      value:\n"
            '        - [1, "hello"]\n'
            "teardown:\n"
            '  - "DROP TABLE IF EXISTS t_mock"\n'
        )


def get_ai_client(config: dict) -> AIClient:
    """Factory: create an AI client based on config."""
    provider = config.get("provider", "openai")
    api_key = config.get("api_key", "")

    if api_key.startswith("${") and api_key.endswith("}"):
        env_var = api_key[2:-1]
        api_key = os.environ.get(env_var, "")

    if not api_key:
        logger.warning(
            "No AI API key configured — using MockAIClient. "
            "Set ai.api_key in config.yaml or OPENAI_API_KEY env var."
        )
        return MockAIClient()

    if provider == "openai":
        model = config.get("model", "gpt-4")
        temperature = config.get("temperature", 0.3)
        return OpenAIClient(api_key=api_key, model=model, temperature=temperature)

    logger.warning("Unknown AI provider '%s', falling back to mock", provider)
    return MockAIClient()
