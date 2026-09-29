import json

import httpx
import pytest

from discordbot.llm import LLMClient, LLMUnavailable

SCHEMA = {"type": "object", "properties": {"a": {"type": "integer"}}}
MSGS = [{"role": "user", "content": "hi"}]


def client(handler, **kw) -> LLMClient:
    return LLMClient(base_url="http://ollama.test", transport=httpx.MockTransport(handler), **kw)


def reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


async def test_schema_pass_through_and_json_parse():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return reply('{"a": 1}')

    out = await client(handler).chat(MSGS, schema=SCHEMA, model="m")
    assert out == {"a": 1}
    assert seen["format"] == SCHEMA
    assert seen["think"] is False
    assert seen["stream"] is False
    assert seen["model"] == "m"


async def test_plain_text_without_schema():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "format" not in json.loads(request.content)
        return reply("hello")

    assert await client(handler).chat(MSGS) == "hello"


async def test_default_model_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "custom")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return reply("x")

    await client(handler).chat(MSGS)
    assert seen["model"] == "custom"


async def test_timeout_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(LLMUnavailable):
        await client(handler).chat(MSGS)


async def test_connection_error_raises_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(LLMUnavailable):
        await client(handler).chat(MSGS)


async def test_invalid_json_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        await client(lambda r: reply("not json")).chat(MSGS, schema=SCHEMA)


async def test_http_error_raises_unavailable():
    with pytest.raises(LLMUnavailable):
        await client(lambda r: httpx.Response(500)).chat(MSGS)
