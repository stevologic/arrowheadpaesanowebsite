"""Arrowhead Paesano — automated Kansas City Chiefs weekly narrative engine.

This package collects live Chiefs signals (schedule, news wires), figures out
where the season is (training camp, preseason, a specific game week, playoffs,
offseason), asks a writer (an LLM provider or a deterministic offline writer) to
turn all of it into a structured "Chiefs Narrative", renders X's-and-O's field
diagrams for it, and writes everything the Hugo site needs to render the
`/narrative/` desk plus a YouTube run-of-show.

Everything is designed to run three ways with the same output:

* Locally with Claude Code / Codex (CLI providers) or an API key.
* In a scheduled GitHub Action with an ``OPENAI_API_KEY`` (or Anthropic key).
* With **no** key at all — the offline writer still produces a complete,
  source-cited edition from the live wire so the site always moves forward.
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
