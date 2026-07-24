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
import re
import shutil
import subprocess
import tempfile
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
                      system: str, user: str) -> str:
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
            timeout=120,
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
    resp = _post(payload)
    if resp.status_code == 400:
        # Some models/endpoints do not accept response_format — retry plain.
        payload.pop("response_format", None)
        resp = _post(payload)
    if resp.status_code >= 400:
        raise ProviderError(f"{label} {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as exc:
        raise ProviderError(f"{label}: unexpected response shape ({exc})") from exc


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
GROK_DEFAULT_MODEL = "grok-4"
GROK_DEFAULT_BASE = "https://api.x.ai/v1"


def grok_key() -> str:
    """Accept either XAI_API_KEY or GROK_API_KEY."""
    return config.env("XAI_API_KEY") or config.env("GROK_API_KEY")


def grok_model() -> str:
    return config.env("GROK_MODEL") or config.env("XAI_MODEL") or GROK_DEFAULT_MODEL


def _grok(system: str, user: str) -> str:
    return _chat_completions(
        "Grok",
        config.env("XAI_BASE_URL") or config.env("GROK_BASE_URL") or GROK_DEFAULT_BASE,
        grok_key(),
        grok_model(),
        system,
        user,
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
            "max_tokens": 4096,
            "temperature": 0.55,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=120,
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
