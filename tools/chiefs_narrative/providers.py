"""Pluggable writer backends.

Selection order (unless ``CHIEFS_PROVIDER`` forces one):
1. OpenAI      (OPENAI_API_KEY)          — GitHub Action default.
2. Anthropic   (ANTHROPIC_API_KEY).
3. claude CLI  (`claude` on PATH)        — local Claude Code.
4. codex CLI   (`codex` on PATH)         — local ChatGPT Codex.
5. offline     — deterministic writer, always available, no key.

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
def _openai(system: str, user: str) -> str:
    key = config.env("OPENAI_API_KEY")
    model = config.env("OPENAI_MODEL") or "gpt-4o"
    base = config.env("OPENAI_BASE_URL") or "https://api.openai.com/v1"
    resp = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.55,
            "response_format": {"type": "json_object"},
        },
        timeout=120,
    )
    if resp.status_code >= 400:
        raise ProviderError(f"OpenAI {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


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
    "openai": _openai,
    "anthropic": _anthropic,
    "claude-cli": _claude_cli,
    "codex-cli": _codex_cli,
}


def resolve_provider() -> str:
    forced = config.env("CHIEFS_PROVIDER").lower()
    if forced:
        return forced
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
    if name == "openai":
        label = f"openai:{config.env('OPENAI_MODEL') or 'gpt-4o'}"
    elif name == "anthropic":
        label = f"anthropic:{config.env('ANTHROPIC_MODEL') or 'claude-sonnet-5'}"
    return data, label
