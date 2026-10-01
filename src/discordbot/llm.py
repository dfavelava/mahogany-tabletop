import json
import os
from typing import Any

import httpx

OLLAMA_BASE_URL_ENV = "OLLAMA_BASE_URL"
OLLAMA_MODEL_ENV = "OLLAMA_MODEL"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"
DEFAULT_TIMEOUT = 60.0


class LLMUnavailable(Exception):
    """Ollama is unreachable, timed out, or returned an unusable response.

    Callers catch this to degrade (skip the LLM step) rather than fail.
    """


class LLMClient:
    """Thin async client over Ollama's /api/chat.

    Nothing touches the network until `chat` is called, so constructing one
    (and starting the bot) works while Ollama is down.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv(OLLAMA_BASE_URL_ENV) or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.getenv(OLLAMA_MODEL_ENV) or DEFAULT_MODEL
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def chat(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | str:
        """Send `messages` and return the reply.

        With `schema`, Ollama constrains output to it (`format=<schema>`) and
        the parsed JSON object is returned; otherwise the reply text.
        Raises LLMUnavailable on connection errors, timeouts, HTTP errors, or
        a reply that isn't valid JSON when a schema was given.
        """
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,
        }
        if schema is not None:
            payload["format"] = schema
        try:
            resp = await self._http.post("/api/chat", json=payload)
            resp.raise_for_status()
            content = resp.json()["message"]["content"]
        except httpx.HTTPError as e:
            raise LLMUnavailable(f"Ollama request failed: {e!r}") from e
        except (ValueError, KeyError, TypeError) as e:
            raise LLMUnavailable(f"Unexpected Ollama response: {e!r}") from e
        if schema is None:
            return content
        try:
            return json.loads(content)
        except ValueError as e:
            raise LLMUnavailable(f"Ollama returned invalid JSON: {e!r}") from e

    async def aclose(self) -> None:
        await self._http.aclose()
