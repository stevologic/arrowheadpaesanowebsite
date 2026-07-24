"""End-to-end: collect -> phase -> write -> render diagrams -> write JSON.

Run it directly::

    python -m tools.chiefs_narrative.generate            # auto-pick provider
    python -m tools.chiefs_narrative.generate --provider offline
    python -m tools.chiefs_narrative.generate --dry-run  # print, don't write

Environment (all optional):
    CHIEFS_PROVIDER   force one of: openai|anthropic|claude-cli|codex-cli|offline
    OPENAI_API_KEY / OPENAI_MODEL / OPENAI_BASE_URL
    ANTHROPIC_API_KEY / ANTHROPIC_MODEL
    ODDS_API_KEY      optional sportsbook consensus via The Odds API
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from . import collect, config, diagrams, odds, offline, phase as phase_mod
from . import prompts, providers, schema


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return text or "play"


def _render_diagrams(narrative: dict) -> None:
    """Render each X&O concept to an SVG and attach the file path + side."""
    config.ensure_dirs()
    used = {}
    for i, xo in enumerate(narrative.get("xsandos", []), 1):
        concept = xo.get("concept", diagrams.DEFAULT_CONCEPT)
        # Unique slug per edition; concept can repeat across editions (overwrite OK).
        base = f"xo-{concept}"
        slug = base if base not in used else f"{base}-{i}"
        used[base] = True
        info = diagrams.write_diagram(
            config.DIAGRAM_DIR,
            slug,
            concept,
            labels=xo.get("labels"),
            title=xo.get("title"),
            blurb=xo.get("why") or None,
        )
        xo["diagram"] = info["file"]
        xo["side"] = info["side"]


def _write_archive(narrative: dict) -> None:
    """Append a compact snapshot of this edition to the rolling archive."""
    try:
        archive = json.loads(config.ARCHIVE_JSON.read_text(encoding="utf-8"))
        if not isinstance(archive, list):
            archive = []
    except Exception:  # noqa: BLE001
        archive = []

    snapshot = {
        "generatedAt": narrative["generatedAt"],
        "edition": narrative["edition"],
        "phase": narrative["phase"]["label"],
        "headline": narrative["headline"],
        "theEdge": narrative.get("theEdge", ""),
    }
    # Replace a same-day snapshot rather than duplicating.
    today = narrative["generatedAt"][:10]
    archive = [a for a in archive if (a.get("generatedAt", "")[:10] != today)]
    archive.insert(0, snapshot)
    archive = archive[:30]  # keep the last ~month of editions
    config.ARCHIVE_JSON.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_schedule(schedule: list[dict]) -> None:
    if schedule:
        config.SCHEDULE_JSON.write_text(
            json.dumps(schedule, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def build(provider_name: str | None = None) -> dict:
    print("Arrowhead Paesano — Chiefs Narrative engine")
    print("-" * 52)

    # 1. Collect live signals.
    signals = collect.collect_all()
    schedule = signals["schedule"]

    # 2. Determine phase + upcoming games.
    ph = phase_mod.detect(schedule)
    upcoming = phase_mod.next_games(schedule, count=3)
    print(f"  [phase] {ph['label']} (type={ph['type']}, mode={ph['mode']})")

    # 3. Markets/predictions for the next game.
    next_game = ph.get("nextGame") or (upcoming[0] if upcoming else None)
    signals["markets"] = odds.collect_markets(next_game)

    # 4. Choose a writer.
    name = provider_name or providers.resolve_provider()
    print(f"  [writer] provider = {name}")

    raw = None
    generator_label = "offline"
    if name != "offline":
        try:
            system = prompts.SYSTEM_PROMPT
            user = prompts.build_user_prompt(signals, ph, upcoming)
            raw, generator_label = providers.generate_via_llm(name, system, user)
            print(f"  [writer] LLM reply parsed ({generator_label})")
        except Exception as exc:  # noqa: BLE001 - fall back, never hard-fail
            print(f"  [writer] provider '{name}' failed ({exc}); using offline writer")
            raw = None

    if raw is None:
        raw = offline.write(signals, ph, upcoming)
        generator_label = "offline"

    # 5. Normalize + validate.
    meta = {
        "generatedAt": config.iso_now(),
        "generator": generator_label,
        "record": config.TEAM["last_season_record"],
        "markets": signals.get("markets", {}),
    }
    narrative = schema.normalize(raw, phase=ph, meta=meta)

    # 6. Render diagrams and attach files.
    _render_diagrams(narrative)

    return {"narrative": narrative, "schedule": schedule}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Chiefs Narrative edition.")
    parser.add_argument("--provider", help="force provider (openai|anthropic|claude-cli|codex-cli|offline)")
    parser.add_argument("--dry-run", action="store_true", help="print JSON, do not write files")
    args = parser.parse_args(argv)

    result = build(args.provider)
    narrative = result["narrative"]

    if args.dry_run:
        print(json.dumps(narrative, indent=2, ensure_ascii=False))
        return 0

    config.ensure_dirs()
    config.NARRATIVE_JSON.write_text(
        json.dumps(narrative, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_archive(narrative)
    _write_schedule(result["schedule"])

    print("-" * 52)
    print(f"  wrote {config.NARRATIVE_JSON.relative_to(config.REPO_ROOT)}")
    print(f"  wrote {config.ARCHIVE_JSON.relative_to(config.REPO_ROOT)}")
    print(f"  edition: {narrative['edition']} — {narrative['headline']}")
    print(f"  generator: {narrative['generator']}")
    print(f"  diagrams: {len(narrative.get('xsandos', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
