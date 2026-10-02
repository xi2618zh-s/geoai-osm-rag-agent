"""Resilient client for Ollama's local JSON chat API."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Dict

import requests

from src.config import (
    OLLAMA_BASE_URL,
    OLLAMA_MAX_RETRIES,
    OLLAMA_TIMEOUT_S,
    RETRY_BACKOFF_S,
)
from src.observability import log_event, new_trace_id
from src.reliability import retry_call


@dataclass
class LLMResult:
    ok: bool
    data: Dict[str, Any]
    raw: str
    error_code: str | None = None
    attempts: int = 1
    latency_ms: float = 0.0


def _retryable_request_error(error: Exception) -> bool:
    if isinstance(error, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return error.response.status_code == 429 or error.response.status_code >= 500
    return False


def _request_error_result(error: Exception, timeout_s: int) -> tuple[str, str]:
    if isinstance(error, requests.Timeout):
        return "OLLAMA_TIMEOUT", f"Ollama request timed out after {timeout_s}s"
    if isinstance(error, requests.ConnectionError):
        return "OLLAMA_UNAVAILABLE", "Cannot connect to Ollama; start it with 'ollama serve'"
    if isinstance(error, requests.HTTPError):
        response = error.response
        status = response.status_code if response is not None else "unknown"
        try:
            detail = response.json().get("error") or response.text
        except (ValueError, AttributeError):
            detail = response.text if response is not None else str(error)
        return "OLLAMA_HTTP_ERROR", f"Ollama HTTP {status}: {str(detail).strip()}"
    if isinstance(error, requests.RequestException):
        return "OLLAMA_REQUEST_ERROR", f"Ollama request failed: {error}"
    return "OLLAMA_UNEXPECTED_ERROR", f"Unexpected Ollama error: {error}"


def call_ollama_json(
    model: str,
    system: str,
    user: str,
    timeout_s: int = OLLAMA_TIMEOUT_S,
    base_url: str = OLLAMA_BASE_URL,
    *,
    retries: int = OLLAMA_MAX_RETRIES,
    backoff_s: float = RETRY_BACKOFF_S,
    trace_id: str | None = None,
) -> LLMResult:
    """Call Ollama and parse JSON, retrying only transient transport failures."""

    trace_id = trace_id or new_trace_id()
    url = f"{base_url}/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    started = time.perf_counter()
    attempts_seen = 0

    def request_once():
        response = requests.post(url, json=payload, timeout=timeout_s)
        response.raise_for_status()
        return response

    def on_retry(error: Exception, attempt: int, delay: float) -> None:
        nonlocal attempts_seen
        attempts_seen = attempt
        log_event(
            "dependency_retry",
            trace_id=trace_id,
            dependency="ollama",
            attempt=attempt,
            delay_s=delay,
            exception_type=type(error).__name__,
        )

    try:
        response, attempts = retry_call(
            request_once,
            retries=retries,
            backoff_s=backoff_s,
            should_retry=_retryable_request_error,
            on_retry=on_retry,
        )
        result = response.json()
        content = result.get("message", {}).get("content", "")
    except Exception as error:
        attempts = max(1, attempts_seen + 1)
        code, message = _request_error_result(error, timeout_s)
        return LLMResult(
            ok=False,
            data={},
            raw=f"Error: {message}",
            error_code=code,
            attempts=attempts,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    parsed = _parse_json_from_content(content)
    parsed.attempts = attempts
    parsed.latency_ms = round((time.perf_counter() - started) * 1000, 2)
    if not parsed.ok:
        parsed.error_code = "OLLAMA_INVALID_JSON"
    return parsed


def _parse_json_from_content(content: str) -> LLMResult:
    """Extract JSON from clean, fenced, or text-wrapped model output."""

    try:
        return LLMResult(ok=True, data=json.loads(content.strip()), raw=content)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```json\s*(.*?)\s*```", content, re.DOTALL)
    if match:
        try:
            return LLMResult(ok=True, data=json.loads(match.group(1)), raw=content)
        except json.JSONDecodeError:
            pass

    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end > start:
        try:
            return LLMResult(ok=True, data=json.loads(content[start : end + 1]), raw=content)
        except json.JSONDecodeError:
            pass
    return LLMResult(
        ok=False,
        data={},
        raw=content,
        error_code="OLLAMA_INVALID_JSON",
    )


def check_ollama_available(base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        return requests.get(f"{base_url}/api/tags", timeout=5).status_code == 200
    except requests.RequestException:
        return False


def list_available_models(base_url: str = OLLAMA_BASE_URL) -> list:
    try:
        response = requests.get(f"{base_url}/api/tags", timeout=10)
        if response.status_code == 200:
            return [item["name"] for item in response.json().get("models", [])]
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return []
