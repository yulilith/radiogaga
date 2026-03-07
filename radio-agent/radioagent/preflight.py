from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from radioagent.config import Settings


ANTHROPIC_MODELS_URL = "https://api.anthropic.com/v1/models"
ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ELEVENLABS_MODELS_URL = "https://api.elevenlabs.io/v1/models"


class ProviderPreflightError(RuntimeError):
    pass


@dataclass(slots=True)
class HttpResult:
    ok: bool
    status: int
    body: Any


def extract_error_message(payload: Any) -> str:
    if isinstance(payload, dict):
        error_payload = payload.get("error")
        if isinstance(error_payload, dict):
            message = error_payload.get("message")
            if isinstance(message, str) and message:
                return message
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
    if isinstance(payload, str):
        return payload
    return "Unknown error"


def resolve_anthropic_model_id(requested_model: str, models: list[dict[str, Any]]) -> str:
    available_ids = [
        model_id
        for item in models
        if isinstance(item, dict)
        for model_id in [item.get("id")]
        if isinstance(model_id, str)
    ]
    if requested_model in available_ids:
        return requested_model
    prefix_matches = [
        model_id for model_id in available_ids if model_id.startswith(f"{requested_model}-")
    ]
    if prefix_matches:
        return prefix_matches[0]
    return requested_model


def http_json(
    url: str,
    *,
    headers: dict[str, str],
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> HttpResult:
    encoded_body = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(
        url,
        data=encoded_body,
        headers=headers,
        method=method,
    )
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return HttpResult(ok=True, status=response.status, body=parsed)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = raw
        return HttpResult(ok=False, status=exc.code, body=parsed)
    except Exception as exc:
        return HttpResult(
            ok=False,
            status=0,
            body={"error": {"message": f"{type(exc).__name__}: {exc}"}},
        )


def validate_anthropic(settings: Settings) -> str | None:
    if not settings.anthropic_api_key:
        return "Anthropic preflight failed: `ANTHROPIC_API_KEY` is missing."

    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    models_result = http_json(ANTHROPIC_MODELS_URL, headers=headers)
    if not models_result.ok:
        return (
            "Anthropic preflight failed while listing models: "
            f"{extract_error_message(models_result.body)}"
        )

    models = models_result.body.get("data", []) if isinstance(models_result.body, dict) else []
    selected_model = resolve_anthropic_model_id(settings.anthropic_model, models)
    message_result = http_json(
        ANTHROPIC_MESSAGES_URL,
        headers=headers,
        method="POST",
        body={
            "model": selected_model,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        },
    )
    if message_result.ok:
        return None

    error_message = extract_error_message(message_result.body)
    if "credit balance is too low" in error_message.lower():
        return (
            "Anthropic preflight failed: your Anthropic API key is valid, "
            "but the account does not have enough credits to generate turns."
        )
    return f"Anthropic preflight failed: {error_message}"


def validate_elevenlabs(settings: Settings) -> str | None:
    if not settings.elevenlabs_api_key:
        return "ElevenLabs preflight failed: `ELEVENLABS_API_KEY` is missing."

    result = http_json(
        ELEVENLABS_MODELS_URL,
        headers={"xi-api-key": settings.elevenlabs_api_key},
    )
    body = result.body if isinstance(result.body, list) else []
    has_requested_model = any(
        isinstance(item, dict) and item.get("model_id") == settings.elevenlabs_model
        for item in body
    )
    if not result.ok:
        if result.status == 401:
            return "ElevenLabs preflight failed: the API key was rejected with 401 Unauthorized."
        return f"ElevenLabs preflight failed: {extract_error_message(result.body)}"
    if not has_requested_model:
        return (
            "ElevenLabs preflight failed: the requested TTS model "
            f"`{settings.elevenlabs_model}` is not available for this account."
        )
    return None


async def run_provider_preflight(settings: Settings) -> None:
    tasks: list[asyncio.Future[str | None]] = []
    if settings.agent_backend in {"claude_sdk", "anthropic_api"}:
        tasks.append(asyncio.to_thread(validate_anthropic, settings))
    if settings.tts_provider == "elevenlabs":
        tasks.append(asyncio.to_thread(validate_elevenlabs, settings))

    if not tasks:
        return

    results = await asyncio.gather(*tasks)
    failures = [result for result in results if result]
    if failures:
        raise ProviderPreflightError("\n".join(f"- {failure}" for failure in failures))

