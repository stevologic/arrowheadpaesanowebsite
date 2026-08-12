"""End-to-end: collect -> phase -> write -> render diagrams -> write JSON.

Run it directly::

    python -m tools.chiefs_narrative.generate            # auto-pick provider
    python -m tools.chiefs_narrative.generate --provider grok
    python -m tools.chiefs_narrative.generate --provider offline
    python -m tools.chiefs_narrative.generate --dry-run  # print, don't write

Environment (all optional):
    CHIEFS_PROVIDER   force one of: grok|openai|anthropic|claude-cli|codex-cli|offline
    XAI_API_KEY (or GROK_API_KEY) / GROK_MODEL / XAI_BASE_URL  — Grok wins over OpenAI
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


def _ensure_six_xsandos(narrative: dict, signals: dict, ph: dict, upcoming: list) -> None:
    """Guarantee exactly six X&O cards.

    The prompt demands six, but if a writer under-delivers we top up from the
    offline writer's phase-aware cards, preferring concepts not already used.
    """
    want = 6
    cards = narrative.get("xsandos") or []
    if len(cards) >= want:
        narrative["xsandos"] = cards[:want]
        return
    pool = schema._norm_xsandos(offline.write(signals, ph, upcoming).get("xsandos"))
    used = {c["concept"] for c in cards}
    for card in pool:  # distinct concepts first
        if len(cards) >= want:
            break
        if card["concept"] not in used:
            cards.append(card)
            used.add(card["concept"])
    for card in pool:  # last resort: allow a repeated concept
        if len(cards) >= want:
            break
        cards.append(card)
    narrative["xsandos"] = cards


def _edition_slug(narrative: dict) -> str:
    """Stable per-edition slug from the generation timestamp, e.g. 2026-07-25-1930."""
    stamp = (narrative.get("generatedAt") or config.iso_now())[:16]  # YYYY-MM-DDTHH:MM
    return stamp.replace("T", "-").replace(":", "")


def _render_diagrams(narrative: dict) -> None:
    """Render each X&O concept to an SVG and attach the file path + side.

    Diagrams live in a per-edition directory so archived editions keep their
    own boards instead of being overwritten by the next day's run.
    """
    config.ensure_dirs()
    edition = narrative.get("slug") or _edition_slug(narrative)
    out_dir = config.DIAGRAM_DIR / edition
    used = {}
    for i, xo in enumerate(narrative.get("xsandos", []), 1):
        concept = xo.get("concept", diagrams.DEFAULT_CONCEPT)
        base = f"xo-{concept}"
        slug = base if base not in used else f"{base}-{i}"
        used[base] = True
        info = diagrams.write_diagram(
            out_dir,
            slug,
            concept,
            labels=xo.get("labels"),
            title=xo.get("title"),
            blurb=xo.get("why") or None,
        )
        xo["diagram"] = f"images/narrative/{edition}/{slug}.svg"
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
        "slug": narrative.get("slug", ""),
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
    if name in ("grok", "xai"):
        print(f"  [writer] model = {providers.grok_model()}")

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
    narrative["slug"] = _edition_slug(narrative)

    # 6. Guarantee six X&O cards, then render diagrams and attach files.
    _ensure_six_xsandos(narrative, signals, ph, upcoming)
    _render_diagrams(narrative)

    return {"narrative": narrative, "schedule": schedule}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Chiefs Narrative edition.")
    parser.add_argument("--provider", help="force provider (grok|openai|anthropic|claude-cli|codex-cli|offline)")
    parser.add_argument("--dry-run", action="store_true", help="print JSON, do not write files")
    args = parser.parse_args(argv)

    result = build(args.provider)
    narrative = result["narrative"]

    if args.dry_run:
        print(json.dumps(narrative, indent=2, ensure_ascii=False))
        return 0

    config.ensure_dirs()
    payload = json.dumps(narrative, indent=2, ensure_ascii=False) + "\n"
    config.NARRATIVE_JSON.write_text(payload, encoding="utf-8")
    # Full copy per edition so archived editions stay viewable as their own pages.
    (config.EDITIONS_DIR / f"{narrative['slug']}.json").write_text(payload, encoding="utf-8")
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
