import asyncio
import json
import time

import httpx
import pytest

from newsfeed.ai import (AIError, AnthropicProvider, OllamaProvider, OpenAICompatibleProvider, parse_json_reply,
                         provider_from_env)
from newsfeed.demo import demo_items
from newsfeed.store import Store
from newsfeed.trending import TrendingEngine, TrendingResult, ai_trending

REPLY = {"stories": [{"headline": "VPN flaw exploited", "summary": "s", "why_trending": "w",
                      "category": "security", "item_ids": [17, 18], "momentum": 80}]}


def chat_completion(content, finish_reason="stop"):
    return {"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}]}


def test_schema_is_strict_json_schema_compatible():
    schema = TrendingResult.model_json_schema()
    story = schema["$defs"]["TrendingStory"]
    assert schema["additionalProperties"] is False and story["additionalProperties"] is False
    assert set(story["required"]) == set(story["properties"])
    assert story["properties"]["category"]["enum"] == ["world", "ai", "tech", "security"]


@pytest.mark.parametrize("text", [
    json.dumps(REPLY),
    "```json\n" + json.dumps(REPLY) + "\n```",
    "Here are the stories:\n" + json.dumps(REPLY) + "\nHope this helps.",
])
def test_parse_json_reply_tolerates_wrapping(text):
    assert parse_json_reply(text, TrendingResult).stories[0].headline == "VPN flaw exploited"


def test_parse_json_reply_rejects_bad_output():
    with pytest.raises(AIError, match="no JSON"):
        parse_json_reply("sorry, I can't", TrendingResult)
    with pytest.raises(AIError, match="schema"):
        parse_json_reply('{"stories": [{"headline": "x"}]}', TrendingResult)


def test_openai_compatible_request_and_fallback_to_json_object():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((request.url.path, request.headers.get("authorization"), body.get("response_format", {}).get("type")))
        if body.get("response_format", {}).get("type") == "json_schema":
            return httpx.Response(400, json={"error": "response_format json_schema not supported"})
        return httpx.Response(200, json=chat_completion("```json\n" + json.dumps(REPLY) + "\n```"))

    provider = OpenAICompatibleProvider(model="my-model", base_url="http://llm.internal:8080/v1/",
                                        api_key="sk-test", transport=httpx.MockTransport(handler))
    now = time.time()
    stories = asyncio.run(ai_trending(demo_items(now), now, provider))

    assert stories[0]["headline"] == "VPN flaw exploited"
    assert seen == [("/v1/chat/completions", "Bearer sk-test", "json_schema"),
                    ("/v1/chat/completions", "Bearer sk-test", "json_object")]
    assert provider.json_mode == "json_object"  # remembered for the next request


def test_openai_compatible_sends_schema_and_no_auth_without_key():
    bodies = []

    def handler(request):
        bodies.append((request.headers.get("authorization"), json.loads(request.content)))
        return httpx.Response(200, json=chat_completion(json.dumps(REPLY)))

    provider = OpenAICompatibleProvider(model="m", base_url="http://localhost:1234/v1", transport=httpx.MockTransport(handler))
    asyncio.run(provider.generate("sys", "user", TrendingResult))
    auth, body = bodies[0]
    assert auth is None
    assert body["model"] == "m" and body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]["$defs"]["TrendingStory"]


@pytest.mark.parametrize("status,payload,match", [
    (401, {"error": "bad key"}, "HTTP 401"),
    (200, chat_completion('{"stories": [', finish_reason="length"), "cut off"),
    (200, {"unexpected": True}, "response shape"),
])
def test_openai_compatible_errors(status, payload, match):
    provider = OpenAICompatibleProvider(model="m", transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))
    with pytest.raises(AIError, match=match):
        asyncio.run(provider.generate("s", "u", TrendingResult))


def test_ollama_uses_native_chat_with_format_and_context():
    bodies = []

    def handler(request):
        assert request.url.path == "/api/chat"
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(REPLY)},
                                         "done": True, "done_reason": "stop"})

    # A /v1 suffix (the OpenAI-compatible path) is stripped so the native API is used.
    provider = OllamaProvider(model="qwen2.5:14b", base_url="http://ollama:11434/v1", num_ctx=16384,
                              transport=httpx.MockTransport(handler))
    result = asyncio.run(provider.generate("sys", "user", TrendingResult))
    assert result.stories[0].category == "security"
    body = bodies[0]
    assert body["model"] == "qwen2.5:14b" and body["stream"] is False
    assert body["options"]["num_ctx"] == 16384
    assert body["format"]["$defs"]["TrendingStory"]


def test_ollama_missing_model_is_actionable():
    provider = OllamaProvider(model="nope", transport=httpx.MockTransport(lambda r: httpx.Response(404, json={"error": "model 'nope' not found"})))
    with pytest.raises(AIError, match="ollama pull nope"):
        asyncio.run(provider.generate("s", "u", TrendingResult))


def test_provider_from_env(monkeypatch):
    for var in ("NEWSFEED_AI_PROVIDER", "NEWSFEED_MODEL", "NEWSFEED_DISABLE_AI", "ANTHROPIC_API_KEY",
                "ANTHROPIC_AUTH_TOKEN", "OPENAI_BASE_URL", "OPENAI_API_KEY", "OLLAMA_BASE_URL",
                "NEWSFEED_TRENDING_MAX_ITEMS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("newsfeed.ai._anthropic_has_credentials", lambda: False)
    assert provider_from_env() is None  # nothing configured: keyword clustering

    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "ollama")
    with pytest.raises(AIError, match="NEWSFEED_MODEL"):
        provider_from_env()
    monkeypatch.setenv("NEWSFEED_MODEL", "llama3.1:8b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama:11434")
    p = provider_from_env()
    assert isinstance(p, OllamaProvider) and p.base_url == "http://ollama:11434" and p.max_items == 150

    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://vllm:8000/v1")
    monkeypatch.setenv("NEWSFEED_TRENDING_MAX_ITEMS", "250")
    p = provider_from_env()
    assert isinstance(p, OpenAICompatibleProvider) and p.base_url == "http://vllm:8000/v1" and p.max_items == 250

    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "anthropic")
    with pytest.raises(AIError, match="ANTHROPIC_API_KEY"):
        provider_from_env()
    monkeypatch.setattr("newsfeed.ai._anthropic_has_credentials", lambda: True)
    monkeypatch.delenv("NEWSFEED_MODEL")
    assert isinstance(provider_from_env(), AnthropicProvider)

    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "none")
    assert provider_from_env() is None
    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "gemini")
    with pytest.raises(AIError, match="unknown"):
        provider_from_env()


def test_engine_reports_misconfiguration_and_falls_back(monkeypatch):
    monkeypatch.setenv("NEWSFEED_AI_PROVIDER", "openai")
    monkeypatch.delenv("NEWSFEED_MODEL", raising=False)
    store = Store()
    store.add(demo_items(time.time()))
    engine = TrendingEngine(store)
    asyncio.run(engine.update(force=True))
    snap = engine.snapshot()
    assert snap["mode"] == "heuristic" and snap["stories"] and "NEWSFEED_MODEL" in snap["error"]


def test_engine_records_provider_and_model():
    provider = OllamaProvider(model="llama3.1:8b", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"message": {"content": json.dumps(REPLY)}, "done_reason": "stop"})))
    store = Store()
    store.add(demo_items(time.time()))
    engine = TrendingEngine(store, provider=provider)
    asyncio.run(engine.update(force=True))
    snap = engine.snapshot()
    assert (snap["mode"], snap["provider"], snap["model"], snap["error"]) == ("ai", "ollama", "llama3.1:8b", None)
    restored = TrendingEngine(store, provider=provider).snapshot()
    assert (restored["provider"], restored["model"]) == ("ollama", "llama3.1:8b")
