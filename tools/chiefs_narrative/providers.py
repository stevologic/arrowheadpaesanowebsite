"""Pluggable writer backends.

Selection order (unless ``CHIEFS_PROVIDER`` forces one):
1. Grok/xAI    (XAI_API_KEY or GROK_API_KEY) — takes precedence over OpenAI.
2. OpenAI      (OPENAI_API_KEY).
3. Anthropic   (ANTHROPIC_API_KEY).
4. claude CLI  (`claude` on PATH)        — local Claude Code.
5. codex CLI   (`codex` on PATH)         — local ChatGPT Codex.
6. offline     — deterministic writer, always available, no key.

Every provider returns a raw JSON string; callers extract/validate it. API
providers use plain ``requests`` so there is no SDK to install in CI.
"""
from __future__ import annotations

import json
import time
import re
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import requests

from . import config


class ProviderError(RuntimeError):
    pass


def extract_json(text: str) -> dict:
    """Pull the first balanced JSON object out of a model reply."""
    if not text:
        raise ProviderError("empty model reply")
    text = text.strip()
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start = text.find("{")
    if start == -1:
        raise ProviderError("no JSON object in reply")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])
    raise ProviderError("unbalanced JSON in reply")


# ---------------------------------------------------------------------------
# API providers
# ---------------------------------------------------------------------------
def _chat_completions(label: str, base: str, key: str, model: str,
                      system: str, user: str, stream: bool = False) -> str:
    """Call any OpenAI-compatible /chat/completions endpoint.

    Used by both OpenAI and Grok (xAI), whose APIs share this wire format. If the
    server rejects ``response_format`` we retry once without it — ``extract_json``
    can recover JSON from a plain reply anyway.
    """
    if not key:
        raise ProviderError(f"{label}: no API key configured")

    def _post(payload):
        return requests.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            # (connect, read-between-bytes). Streaming keeps bytes flowing, so the
            # read timeout is an idle limit, not a whole-generation limit.
            timeout=(20, llm_timeout()),
            stream=bool(payload.get("stream")),
        )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.55,
        "response_format": {"type": "json_object"},
    }
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        # Bound Grok-4.7 think time. Default "low"; override via env.
        payload["reasoning_effort"] = config.env("GROK_REASONING_EFFORT") or "low"
        try:
            payload["max_tokens"] = int(config.env("LLM_MAX_TOKENS") or 8192)
        except ValueError:
            payload["max_tokens"] = 8192
    resp = None
    try:
        resp = _post(payload)
        if resp.status_code == 400 and "response_format" in resp.text.lower():
            # Only retry an explicitly unsupported JSON format, never an
            # invalid model or another unrelated 400 (another paid request).
            resp.close()
            payload.pop("response_format", None)
            resp = _post(payload)
        if resp.status_code == 400 and "reasoning_effort" in resp.text.lower():
            # Same gate as response_format: drop only this rejected field.
            resp.close()
            payload.pop("reasoning_effort", None)
            resp = _post(payload)
        if resp.status_code >= 400:
            raise ProviderError(f"{label} {resp.status_code}: {resp.text[:300]}")
        if stream:
            return _read_stream(label, resp)
        return resp.json()["choices"][0]["message"]["content"]
    except requests.RequestException as exc:
        raise ProviderError(f"{label}: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise ProviderError(f"{label}: unexpected response shape ({exc})") from exc
    finally:
        if resp is not None:
            resp.close()


def llm_deadline() -> int:
    """Wall-clock cap for one streamed generation (seconds)."""
    try:
        return int(config.env("LLM_DEADLINE") or 420)
    except ValueError:
        return 420


def _read_stream(label: str, resp) -> str:
    """Accumulate an OpenAI-compatible SSE stream into the final content string.

    A non-streamed frontier-model call can sit silent for minutes while it
    reasons, and api.x.ai (or a proxy in front of it) drops idle connections
    ("Remote end closed connection" / 300s read timeout). Streaming keeps the
    socket busy and lets us enforce a wall-clock deadline ourselves.
    """
    started = time.monotonic()
    parts: list[str] = []
    complete = False
    usage = None
    # text/event-stream often arrives without a charset; requests then assumes
    # ISO-8859-1 and turns UTF-8 "·" / "–" into mojibake ("Â·", "â€“").
    resp.encoding = "utf-8"
    for raw in resp.iter_lines(decode_unicode=True):
        if time.monotonic() - started > llm_deadline():
            resp.close()
            raise ProviderError(f"{label}: stream exceeded {llm_deadline()}s deadline")
        if not raw or not raw.startswith("data:"):
            continue
        data = raw[5:].strip()
        if data == "[DONE]":
            complete = True
            break
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        if chunk.get("error"):
            raise ProviderError(f"{label}: stream error {str(chunk['error'])[:300]}")
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            reason = choice.get("finish_reason")
            if reason and reason != "stop":
                raise ProviderError(f"{label}: incomplete stream (finish_reason={reason})")
            piece = (choice.get("delta") or {}).get("content")
            if piece:
                parts.append(piece)
    text = "".join(parts)
    if not text.strip():
        raise ProviderError(f"{label}: empty streamed reply")
    if not complete:
        raise ProviderError(f"{label}: stream ended before [DONE]")
    if usage is not None:
        # Log provider measurements only; do not estimate dollars or tokens.
        print(f"  [writer] {label} token usage: {json.dumps(usage, sort_keys=True)}")
    return text


# --- OpenAI ---------------------------------------------------------------
def _openai(system: str, user: str) -> str:
    return _chat_completions(
        "OpenAI",
        config.env("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        config.env("OPENAI_API_KEY"),
        openai_model(),
        system,
        user,
    )


def openai_model() -> str:
    return config.env("OPENAI_MODEL") or "gpt-4o"


# --- Grok (xAI) -----------------------------------------------------------
# xAI ships an OpenAI-compatible API, so it reuses the same helper.
GROK_DEFAULT_MODEL = "grok-4.7"
GROK_DEFAULT_BASE = "https://api.x.ai/v1"


def llm_timeout() -> int:
    """HTTP read timeout for LLM calls. A full six-card edition can take a
    frontier model well past two minutes, so default generously."""
    try:
        return int(config.env("LLM_TIMEOUT") or 300)
    except ValueError:
        return 300


def grok_key() -> str:
    """Accept either XAI_API_KEY or GROK_API_KEY."""
    return config.env("XAI_API_KEY") or config.env("GROK_API_KEY")


def grok_model() -> str:
    model = config.env("GROK_MODEL") or config.env("XAI_MODEL") or GROK_DEFAULT_MODEL
    if model == "latest":
        return _latest_grok_model(grok_base(), grok_key())
    return model


def grok_base() -> str:
    return config.env("XAI_BASE_URL") or config.env("GROK_BASE_URL") or GROK_DEFAULT_BASE


@lru_cache(maxsize=1)
def _latest_grok_model(base: str, key: str) -> str:
    """Resolve account-visible stable numbered text models once per process.

    Dated, reasoning, multi-agent, build, and Imagine variants are separate
    products, not upgrades to this article writer. Never select below 4.7.
    """
    if not key:
        raise ProviderError("Grok: no API key configured for latest-model discovery")
    resp = None
    try:
        resp = requests.get(
            f"{base.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {key}"}, timeout=(20, 20),
        )
        if resp.status_code != 200:
            raise ProviderError(f"Grok: model discovery returned HTTP {resp.status_code}")
        candidates = []
        for row in resp.json().get("data", []):
            model = str(row.get("id") or "")
            match = re.fullmatch(r"grok-(\d+)(?:\.(\d+))?", model)
            if match:
                version = (int(match[1]), int(match[2] or 0))
                if version >= (4, 7):
                    candidates.append((version, model))
        if not candidates:
            raise ProviderError("Grok: no stable article model at grok-4.7 or newer is available")
        model = max(candidates)[1]
        print(f"  [writer] latest account-visible Grok article model = {model}")
        return model
    except (requests.RequestException, ValueError, TypeError, AttributeError) as exc:
        raise ProviderError(f"Grok: model discovery failed ({exc})") from exc
    finally:
        if resp is not None:
            resp.close()


def _grok(system: str, user: str) -> str:
    # One selected model. A timeout/error must never buy an older-model draft.
    return _chat_completions(
        "Grok", grok_base(), grok_key(), grok_model(), system, user, stream=True
    )


def _anthropic(system: str, user: str) -> str:
    key = config.env("ANTHROPIC_API_KEY")
    model = config.env("ANTHROPIC_MODEL") or "claude-sonnet-5"
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 8192,
            "temperature": 0.55,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=llm_timeout(),
    )
    if resp.status_code >= 400:
        raise ProviderError(f"Anthropic {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return "".join(block.get("text", "") for block in data.get("content", []))


# ---------------------------------------------------------------------------
# CLI providers (local Claude Code / Codex)
# ---------------------------------------------------------------------------
def _run_cli(cmd: list[str], prompt: str) -> str:
    proc = subprocess.run(
        cmd,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=240,
    )
    if proc.returncode != 0:
        raise ProviderError(
            f"CLI {cmd[0]} exited {proc.returncode}: {proc.stderr[:300]}"
        )
    return proc.stdout


def _claude_cli(system: str, user: str) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise ProviderError("claude CLI not found on PATH")
    prompt = system + "\n\n" + user
    # -p prints the response non-interactively.
    return _run_cli([exe, "-p", prompt], "")


def _codex_cli(system: str, user: str) -> str:
    exe = shutil.which("codex")
    if not exe:
        raise ProviderError("codex CLI not found on PATH")
    prompt = system + "\n\n" + user
    # `codex exec` runs a one-shot prompt and prints the final message.
    return _run_cli([exe, "exec", prompt], "")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
_REGISTRY = {
    "grok": _grok,
    "xai": _grok,  # alias
    "openai": _openai,
    "anthropic": _anthropic,
    "claude-cli": _claude_cli,
    "codex-cli": _codex_cli,
}


def resolve_provider() -> str:
    """Pick a writer. Grok wins when its key is present, then OpenAI, etc."""
    forced = config.env("CHIEFS_PROVIDER").lower()
    if forced:
        return forced
    if grok_key():
        return "grok"
    if config.env("OPENAI_API_KEY"):
        return "openai"
    if config.env("ANTHROPIC_API_KEY"):
        return "anthropic"
    if shutil.which("claude"):
        return "claude-cli"
    if shutil.which("codex"):
        return "codex-cli"
    return "offline"


def generate_via_llm(name: str, system: str, user: str) -> tuple[dict, str]:
    """Call a named LLM provider and return (parsed_json, provider_label)."""
    fn = _REGISTRY.get(name)
    if not fn:
        raise ProviderError(f"unknown provider '{name}'")
    raw = fn(system, user)
    data = extract_json(raw)
    label = name
    if name in ("grok", "xai"):
        label = f"grok:{grok_model()}"
    elif name == "openai":
        label = f"openai:{openai_model()}"
    elif name == "anthropic":
        label = f"anthropic:{config.env('ANTHROPIC_MODEL') or 'claude-sonnet-5'}"
    return data, label
