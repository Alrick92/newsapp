"""AI providers for trending stories.

Three backends return the same validated `TrendingResult`:

- anthropic: Claude via the Anthropic SDK, with structured outputs.
- openai:    any OpenAI-compatible /chat/completions endpoint (OpenAI, vLLM,
             LM Studio, LiteLLM, Together, Groq, ...) set by OPENAI_BASE_URL.
- ollama:    a remote Ollama server's native /api/chat (OLLAMA_BASE_URL, plus
             OLLAMA_API_KEY when it sits behind an authenticating proxy). Unlike
             its /v1 shim, the native API lets us raise the context window so a
             long headline list isn't truncated.

Select one with NEWSFEED_AI_PROVIDER; see `provider_from_env`.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Protocol

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai", "ollama")
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5-5"


class AIError(Exception):
    """The provider could not produce a usable result (misconfigured, failed, refused, bad JSON)."""


class Provider(Protocol):
    name: str
    model: str
    # Newest headlines sent per request; local models get a smaller default.
    max_items: int

    async def generate(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel: ...


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def parse_json_reply(text: str, schema: type[BaseModel]) -> BaseModel:
    """Validate a model's JSON reply, tolerating code fences and surrounding prose."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    try:
        return schema.model_validate_json(cleaned)
    except ValidationError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise AIError("reply contained no JSON object") from None
        try:
            return schema.model_validate_json(cleaned[start:end + 1])
        except ValidationError as exc:
            raise AIError(f"reply did not match the schema: {exc.errors()[0]['msg']}") from None


def schema_instructions(schema: type[BaseModel]) -> str:
    """Appended to the prompt for providers whose JSON enforcement may be weaker than Claude's."""
    return ("\n\nRespond with only a JSON object (no prose, no code fences) that matches this JSON Schema:\n"
            + json.dumps(schema.model_json_schema(), separators=(",", ":")))


# -- Anthropic ---------------------------------------------------------------

class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, client: anthropic.AsyncAnthropic | None = None,
                 max_items: int = 400):
        self.model = model
        self.max_items = max_items
        self.client = client or anthropic.AsyncAnthropic()

    async def generate(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        try:
            response = await self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config={"effort": "medium"},
                # Re-run on a substitute model if a safety classifier declines (e.g. on
                # cyber-heavy security headlines) instead of returning nothing.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AnthropicError as exc:
            raise AIError(f"Anthropic request failed: {exc}") from exc
        if response.stop_reason == "refusal" or response.parsed_output is None:
            raise AIError(f"Anthropic request ended with stop_reason={response.stop_reason}")
        return response.parsed_output


# -- OpenAI-compatible -------------------------------------------------------

class OpenAICompatibleProvider:
    """POST {base_url}/chat/completions, trying json_schema, then json_object, then plain text.

    Endpoints differ in which `response_format` they accept; the first mode that
    isn't rejected with a 400 is remembered for later requests.
    """

    name = "openai"
    JSON_MODES = ("json_schema", "json_object", "none")

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1", api_key: str | None = None,
                 json_mode: str = "json_schema", timeout: float = 300, max_items: int = 150,
                 transport: httpx.AsyncBaseTransport | None = None):
        if json_mode not in self.JSON_MODES:
            raise AIError(f"NEWSFEED_AI_JSON_MODE must be one of {', '.join(self.JSON_MODES)}")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.json_mode = json_mode
        self.timeout = timeout
        self.max_items = max_items
        self._transport = transport

    def _response_format(self, mode: str, schema: type[BaseModel]) -> dict | None:
        if mode == "json_schema":
            return {"type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}}
        if mode == "json_object":
            return {"type": "json_object"}
        return None

    async def generate(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        modes = self.JSON_MODES[self.JSON_MODES.index(self.json_mode):]
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            for mode in modes:
                body = {
                    "model": self.model,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user + schema_instructions(schema)}],
                }
                if fmt := self._response_format(mode, schema):
                    body["response_format"] = fmt
                try:
                    resp = await client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                except httpx.HTTPError as exc:
                    raise AIError(f"{self.base_url} unreachable: {exc}") from exc
                if resp.status_code == 400 and mode != "none":
                    log.info("endpoint rejected response_format=%s; trying the next mode", mode)
                    continue
                if resp.status_code >= 400:
                    raise AIError(f"{self.base_url} returned HTTP {resp.status_code}: {resp.text[:200]}")
                self.json_mode = mode
                try:
                    choice = resp.json()["choices"][0]
                    content = choice["message"].get("content") or ""
                except (ValueError, KeyError, IndexError) as exc:
                    raise AIError("unexpected chat/completions response shape") from exc
                if choice.get("finish_reason") == "length":
                    raise AIError("reply was cut off (finish_reason=length); lower NEWSFEED_TRENDING_MAX_ITEMS")
                return parse_json_reply(content, schema)
        raise AIError("endpoint rejected every response_format")


# -- Ollama ------------------------------------------------------------------

class OllamaProvider:
    """Ollama's native chat API with a JSON-schema `format` and an explicit context size."""

    name = "ollama"

    def __init__(self, model: str, base_url: str, api_key: str | None = None, num_ctx: int = 32768,
                 timeout: float = 600, max_items: int = 150, transport: httpx.AsyncBaseTransport | None = None):
        self.model = model
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.api_key = api_key
        self.num_ctx = num_ctx
        self.timeout = timeout
        self.max_items = max_items
        self._transport = transport

    async def generate(self, system: str, user: str, schema: type[BaseModel]) -> BaseModel:
        body = {
            "model": self.model,
            "stream": False,
            "format": schema.model_json_schema(),
            "options": {"num_ctx": self.num_ctx, "temperature": 0.2},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user + schema_instructions(schema)}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            try:
                resp = await client.post(f"{self.base_url}/api/chat", json=body, headers=headers)
            except httpx.HTTPError as exc:
                raise AIError(f"Ollama at {self.base_url} unreachable: {exc}") from exc
        if resp.status_code in (401, 403):
            raise AIError(f"Ollama at {self.base_url} rejected the request (HTTP {resp.status_code}); check OLLAMA_API_KEY")
        if resp.status_code == 404:
            raise AIError(f"Ollama at {self.base_url} has no model '{self.model}' (pull it on that server: "
                          f"ollama pull {self.model})")
        if resp.status_code >= 400:
            raise AIError(f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
            content = data["message"]["content"]
        except (ValueError, KeyError) as exc:
            raise AIError("unexpected Ollama response shape") from exc
        if data.get("done_reason") == "length":
            raise AIError("reply was cut off; raise NEWSFEED_OLLAMA_NUM_CTX or lower NEWSFEED_TRENDING_MAX_ITEMS")
        return parse_json_reply(content, schema)


# -- configuration -----------------------------------------------------------

def _anthropic_has_credentials() -> bool:
    try:
        client = anthropic.AsyncAnthropic()
    except Exception:  # noqa: BLE001 - any constructor failure means "not configured"
        return False
    return bool(client.api_key or client.auth_token or client.credentials)


def provider_from_env() -> Provider | None:
    """Build the provider named by NEWSFEED_AI_PROVIDER.

    Unset means: Claude if Anthropic credentials are present, otherwise no AI
    (keyword clustering). `none` (or NEWSFEED_DISABLE_AI=1) turns AI off.
    Raises AIError for a named provider that is misconfigured.
    """
    choice = (os.environ.get("NEWSFEED_AI_PROVIDER") or "").strip().lower()
    if os.environ.get("NEWSFEED_DISABLE_AI") == "1" or choice in ("none", "off", "disabled"):
        return None
    model = os.environ.get("NEWSFEED_MODEL") or None
    max_items = os.environ.get("NEWSFEED_TRENDING_MAX_ITEMS")
    timeout = float(os.environ.get("NEWSFEED_AI_TIMEOUT") or 0) or None

    if choice in ("", "anthropic", "claude"):
        if not _anthropic_has_credentials():
            if choice:
                raise AIError("NEWSFEED_AI_PROVIDER=anthropic but no ANTHROPIC_API_KEY is set")
            log.info("no AI provider configured; trending uses keyword clustering")
            return None
        return AnthropicProvider(model=model or DEFAULT_ANTHROPIC_MODEL, max_items=int(max_items or 400))

    if choice in ("openai", "openai-compatible", "custom"):
        if not model:
            raise AIError("NEWSFEED_AI_PROVIDER=openai needs NEWSFEED_MODEL (the endpoint's model name)")
        return OpenAICompatibleProvider(
            model=model,
            base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY") or None,
            json_mode=(os.environ.get("NEWSFEED_AI_JSON_MODE") or "json_schema").lower(),
            timeout=timeout or 300,
            max_items=int(max_items or 150),
        )

    if choice == "ollama":
        if not model:
            raise AIError("NEWSFEED_AI_PROVIDER=ollama needs NEWSFEED_MODEL (a model pulled on the Ollama server)")
        base_url = os.environ.get("OLLAMA_BASE_URL")
        if not base_url:
            raise AIError("NEWSFEED_AI_PROVIDER=ollama needs OLLAMA_BASE_URL (e.g. http://ollama.example.com:11434)")
        return OllamaProvider(
            model=model,
            base_url=base_url,
            api_key=os.environ.get("OLLAMA_API_KEY") or None,
            num_ctx=_env_int("NEWSFEED_OLLAMA_NUM_CTX", 32768),
            timeout=timeout or 600,
            max_items=int(max_items or 150),
        )

    raise AIError(f"unknown NEWSFEED_AI_PROVIDER '{choice}' (use anthropic, openai, ollama or none)")
