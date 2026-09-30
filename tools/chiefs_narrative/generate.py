"""End-to-end: collect -> phase -> write -> render diagrams -> write JSON.

Run it directly::

    python -m tools.chiefs_narrative.generate            # auto-pick provider
    python -m tools.chiefs_narrative.generate --provider grok
    python -m tools.chiefs_narrative.generate --provider offline
    python -m tools.chiefs_narrative.generate --schedule-only  # slate only
    python -m tools.chiefs_narrative.generate --diagrams-only  # SVG from JSON
    python -m tools.chiefs_narrative.generate --check-edition  # review + SVG gate
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
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import collect, config, diagrams, facts, odds, offline, phase as phase_mod
from . import prompts, providers, schema, x_embeds

_CT = ZoneInfo("America/Chicago")

# How many published headlines to show the writer as "do not reuse".
RECENT_HEADLINE_LIMIT = 8
# Initial draft plus this many fact-check rewrites. The failed daily run
# 36358665975 died after one retry on mixed-team sentences.
FACT_CHECK_RETRIES = 2


class DuplicateNarrativeError(RuntimeError):
    """Raised when an edition still clones the most recent published copy after retry."""


class LiveGameSkip(RuntimeError):
    """A Chiefs game is live; do not mint a new edition."""


class FactCheckError(facts.FactCheckError):
    """Review still disagrees with ESPN after one retry; do not publish."""


def _slugify(text: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return text or "play"


def _xo_side(card: dict) -> str:
    spec = diagrams.CONCEPTS.get((card or {}).get("concept") or "")
    return (spec or {}).get("side") or "offense"


def _real_xo_card(card: dict) -> bool:
    if not isinstance(card, dict):
        return False
    return bool((card.get("why") or "").strip() or (card.get("situation") or "").strip())


def _concept_card(key: str, pool: list[dict]) -> dict | None:
    for card in pool:
        if card.get("concept") == key and _real_xo_card(card):
            return card
    spec = diagrams.CONCEPTS.get(key)
    if not spec:
        return None
    return {
        "title": spec["title"],
        "situation": spec["blurb"],
        "concept": key,
        "why": spec["blurb"],
        "coaching": spec["blurb"],
        "labels": {},
    }


def _ensure_six_xsandos(narrative: dict, signals: dict, ph: dict, upcoming: list) -> None:
    """Guarantee six real cards: four offense, two defense.

    Empty DEFAULT_CONCEPT placeholders are dropped. Defense must include
    Cover-2 and the zone blitz — the page promises those two looks.
    Top up from the offline writer; do not pad with empty cards.
    """
    cards = [c for c in (narrative.get("xsandos") or []) if _real_xo_card(c)]
    pool = [
        c
        for c in schema._norm_xsandos(offline.write(signals, ph, upcoming).get("xsandos"))
        if _real_xo_card(c)
    ]
    offense = [c for c in cards if _xo_side(c) == "offense"]
    defense = [c for c in cards if _xo_side(c) == "defense"]

    def _take(side: str, dest: list, want: int) -> None:
        for card in pool:
            if len(dest) >= want:
                return
            if _xo_side(card) != side:
                continue
            if card["concept"] in {c["concept"] for c in dest}:
                continue
            dest.append(card)

    _take("offense", offense, 4)
    for key in ("cover_two", "zone_blitz"):
        if any(c.get("concept") == key for c in defense):
            continue
        extra = _concept_card(key, pool)
        if extra:
            defense.append(extra)
    _take("defense", defense, 2)
    if len(offense) < 4:
        for key, spec in diagrams.CONCEPTS.items():
            if spec["side"] != "offense":
                continue
            if any(c.get("concept") == key for c in offense):
                continue
            extra = _concept_card(key, pool)
            if extra:
                offense.append(extra)
            if len(offense) >= 4:
                break
    offense = offense[:4]
    defense = defense[:2]
    narrative["xsandos"] = offense[:4] + defense[:2]


def _ensure_next_game(narrative: dict, ph: dict) -> None:
    """If the writer skipped nextGame, fill it from the live schedule row."""
    if narrative.get("nextGame", {}).get("opponent"):
        return
    if not ph.get("nextGame"):
        return
    narrative["nextGame"] = schema._norm_next_game(
        phase_mod.format_next_game(ph["nextGame"])
    )


def _ensure_desk_sections(
    narrative: dict, signals: dict, ph: dict, upcoming: list
) -> None:
    """Guarantee last-game / current-state / next-game plan when facts exist."""
    fallback = None

    def _fallback() -> dict:
        nonlocal fallback
        if fallback is None:
            fallback = offline.write(signals, ph, upcoming)
        return fallback

    if ph.get("lastGame") and not narrative.get("lastGameReview", {}).get("lede"):
        narrative["lastGameReview"] = schema._norm_last_game_review(
            _fallback().get("lastGameReview")
        )
    if not narrative.get("currentState", {}).get("lede"):
        narrative["currentState"] = schema._norm_current_state(
            _fallback().get("currentState")
        )
    if not narrative.get("gamePlan", {}).get("lede"):
        narrative["gamePlan"] = schema._norm_game_plan(_fallback().get("gamePlan"))

    review = narrative.get("lastGameReview") or {}
    last = ph.get("lastGame")
    if review:
        if last:
            header = phase_mod.format_last_game(last)
            # ESPN slate facts win over writer copy: date, result, and score
            # are not the model's to invent. Empty ESPN fields must also win
            # so a score-less row cannot leak a model 7–7.
            for key in ("opponent", "label"):
                if header.get(key):
                    review[key] = header[key]
        if last and phase_mod.is_final(last):
            header = phase_mod.format_last_game(last)
            review["result"] = header.get("result") or ""
            review["score"] = header.get("score") or ""
        else:
            review["result"] = ""
            review["score"] = ""
        narrative["lastGameReview"] = review

    nxt = narrative.get("nextGame") or {}
    if ph.get("nextGame") and nxt:
        card = phase_mod.format_next_game(ph["nextGame"])
        for key in ("opponent", "label", "at", "tv"):
            if card.get(key):
                nxt[key] = card[key]
        narrative["nextGame"] = nxt


def _edition_slug(narrative: dict) -> str:
    """Stable per-edition slug from the generation timestamp, e.g. 2026-07-25-1930."""
    stamp = (narrative.get("generatedAt") or config.iso_now())[:16]  # YYYY-MM-DDTHH:MM
    return stamp.replace("T", "-").replace(":", "")


def _edition_calendar_day(payload: dict | None) -> str:
    """America/Chicago calendar day for an edition (YYYY-MM-DD).

    Same-day replacements must share one slug and skip uniqueness against
    the edition they overwrite. UTC midnight is still Tuesday in Kansas City.
    """
    stamp = ((payload or {}).get("generatedAt") or "").strip()
    if stamp:
        try:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_CT).date().isoformat()
        except ValueError:
            pass
    slug = ((payload or {}).get("slug") or "").strip()
    if len(slug) >= 10 and slug[4] == "-" and slug[7] == "-":
        return slug[:10]
    return ""


def _prior_day_editions(recent: list[dict] | None, day: str) -> list[dict]:
    """Recent editions from earlier calendar days — not today's live copy."""
    rows = list(recent or [])
    if not day:
        return rows
    return [ed for ed in rows if _edition_calendar_day(ed) != day]


def _reuse_same_day_slug(narrative: dict, recent: list[dict] | None) -> None:
    """Keep today's published slug so a regen overwrites / and /narrative/."""
    day = _edition_calendar_day(narrative)
    if not day:
        return
    for ed in recent or []:
        if _edition_calendar_day(ed) == day and ed.get("slug"):
            narrative["slug"] = ed["slug"]
            return


def _purge_other_same_day_editions(narrative: dict) -> None:
    """Drop extra same-day edition files so the archive has one slug."""
    day = _edition_calendar_day(narrative)
    keep = (narrative.get("slug") or "").strip()
    if not day or not config.EDITIONS_DIR.is_dir():
        return
    for path in config.EDITIONS_DIR.glob("*.json"):
        if path.stem == keep:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            payload = {"slug": path.stem}
        if not isinstance(payload, dict):
            payload = {"slug": path.stem}
        if _edition_calendar_day(payload) == day:
            path.unlink(missing_ok=True)


def normalize_copy(text) -> str:
    """Case-insensitive, whitespace-normalized copy key for uniqueness checks."""
    return " ".join(str(text or "").lower().split())


def _norm_copy(text) -> str:
    return normalize_copy(text)


def _edition_snapshot(payload: dict) -> dict:
    return {
        "generatedAt": payload.get("generatedAt") or "",
        "slug": payload.get("slug") or "",
        "headline": payload.get("headline") or "",
        "dek": payload.get("dek") or "",
        "theEdge": payload.get("theEdge") or "",
    }


def _load_archive_snapshots(limit: int) -> list[dict]:
    try:
        archive = json.loads(config.ARCHIVE_JSON.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - missing or corrupt archive is "no history"
        return []
    if not isinstance(archive, list):
        return []
    out = []
    for row in archive:
        if not isinstance(row, dict):
            continue
        out.append(_edition_snapshot(row))
        if len(out) >= limit:
            break
    return out


def _load_recent_editions(limit: int = RECENT_HEADLINE_LIMIT) -> list[dict]:
    """Newest-first published editions from disk (not the live website).

    Prefers ``config.EDITIONS_DIR`` sorted by generatedAt, then filename.
    Falls back to ``config.ARCHIVE_JSON`` only when the editions directory
    has nothing usable.
    """
    rows: list[tuple[str, str, dict]] = []
    editions_dir = config.EDITIONS_DIR
    if editions_dir.is_dir():
        for path in editions_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(payload, dict):
                continue
            stamp = (payload.get("generatedAt") or "").strip() or path.stem
            rows.append((stamp, path.name, _edition_snapshot(payload)))
    if rows:
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [snap for _, _, snap in rows[:limit]]
    return _load_archive_snapshots(limit)


def _matched_copy_fields(narrative: dict, previous: dict | None) -> list[str]:
    """Fields whose normalized copy matches the most recent published edition.

    Empty strings do not match — a missing dek/theEdge is not a clone.
    """
    if not previous:
        return []
    matched = []
    for field in ("headline", "dek", "theEdge"):
        left = normalize_copy(narrative.get(field) or "")
        right = normalize_copy(previous.get(field) or "")
        if left and right and left == right:
            matched.append(field)
    return matched


def is_clone(candidate: dict | None, previous: dict | None) -> bool:
    """True if headline, dek, or theEdge matches the previous edition."""
    if not candidate:
        return False
    return bool(_matched_copy_fields(candidate, previous))


def _uniqueness_retry_instruction(previous: dict, matched: list[str]) -> str:
    bits = []
    if "headline" in matched:
        bits.append(f"yesterday's title was {previous.get('headline')}, write a new one")
    if "dek" in matched:
        bits.append(f"yesterday's dek was {previous.get('dek')}, write a new one")
    if "theEdge" in matched:
        bits.append(f"yesterday's theEdge was {previous.get('theEdge')}, write a new one")
    if not bits:
        bits.append("write a new headline, dek, and theEdge")
    return (
        "UNIQUENESS RETRY: the previous published edition already used this copy. "
        + " ".join(bits)
        + " Headline, dek, and theEdge must all be new — do not reprint yesterday."
    )


def _draft_raw(
    name: str, system: str, user: str, signals: dict, ph: dict, upcoming: list
) -> tuple[dict, str]:
    """LLM path with the existing offline fallback. Never hard-fails on provider errors."""
    raw = None
    generator_label = "offline"
    if name != "offline":
        try:
            raw, generator_label = providers.generate_via_llm(name, system, user)
            print(f"  [writer] LLM reply parsed ({generator_label})")
        except Exception as exc:  # noqa: BLE001 - fall back, never hard-fail
            print(f"  [writer] provider '{name}' failed ({exc}); using offline writer")
            raw = None
    if raw is None:
        raw = offline.write(signals, ph, upcoming)
        generator_label = "offline"
    return raw, generator_label


def _assemble_narrative(
    raw: dict, ph: dict, meta: dict, signals: dict, upcoming: list
) -> dict:
    narrative = schema.normalize(raw, phase=ph, meta=meta)
    narrative["edition"] = phase_mod.format_edition(ph)
    narrative["slug"] = _edition_slug(narrative)
    _ensure_next_game(narrative, ph)
    _ensure_desk_sections(narrative, signals, ph, upcoming)
    _ensure_six_xsandos(narrative, signals, ph, upcoming)
    # Models are not given X data and must not invent status IDs. Keep an
    # embed only when oEmbed 200s for an allowlisted official account.
    x_embeds.strip_unverified_embeds(narrative)
    return narrative


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
    today = _edition_calendar_day(narrative)
    archive = [a for a in archive if _edition_calendar_day(a) != today]
    archive.insert(0, snapshot)
    archive = archive[:30]  # keep the last ~month of editions
    config.ARCHIVE_JSON.write_text(
        json.dumps(archive, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_schedule(schedule: list[dict]) -> None:
    collect.write_schedule(schedule)


def _write_wire(headlines: list[dict]) -> None:
    """Publish the collected news wire so the site can render today's headlines."""
    rows = []
    for item in headlines or []:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            continue
        rows.append(
            {
                "title": title,
                "url": url,
                "publisher": (item.get("publisher") or "").strip(),
                "published": item.get("published"),
            }
        )
        if len(rows) >= 12:
            break
    payload = {"updatedAt": config.iso_now(), "headlines": rows}
    config.WIRE_JSON.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def build(provider_name: str | None = None, persist_schedule: bool = True) -> dict:
    print("Arrowhead Paesano — Chiefs Narrative engine")
    print("-" * 52)

    # 1. Collect live signals and persist the slate immediately so a later
    # writer failure still leaves dates, networks, and scores in the repo.
    signals = collect.collect_all()
    schedule = signals["schedule"]
    if persist_schedule:
        _write_schedule(schedule)

    if phase_mod.any_live(schedule):
        raise LiveGameSkip(
            "Chiefs game is live (in progress or past kickoff, not final); "
            "skipping new edition"
        )

    # 2. Determine phase + upcoming games.
    ph = phase_mod.detect(schedule)
    upcoming = phase_mod.next_games(schedule, count=3)
    print(f"  [phase] {ph['label']} (type={ph['type']}, mode={ph['mode']})")

    last = ph.get("lastGame") or {}
    if last.get("id"):
        print(f"  [collect] last-game recap for event {last['id']}…")
        signals["lastGameRecap"] = collect.fetch_game_recap(str(last["id"]))
        prior = collect.prior_completed_game(schedule, last)
        if prior and prior.get("id"):
            print(f"  [collect] prior-game recap for event {prior['id']}…")
            prior_recap = collect.fetch_game_recap(str(prior["id"]))
            if prior_recap:
                prior_recap["opponent"] = prior.get("opponent") or ""
                signals["lastGameRecap"]["prior"] = prior_recap
                signals["priorGameRecap"] = prior_recap

    # 3. Markets/predictions for the next game.
    next_game = ph.get("nextGame") or (upcoming[0] if upcoming else None)
    signals["markets"] = odds.collect_markets(next_game)

    # 4. Choose a writer. Feed recent published titles so the model does not reprint them.
    name = provider_name or providers.resolve_provider()
    print(f"  [writer] provider = {name}")
    if name in ("grok", "xai"):
        print(f"  [writer] model = {providers.grok_model()}")

    recent = _load_recent_editions()
    today = _edition_calendar_day({"generatedAt": config.iso_now()})
    prior_days = _prior_day_editions(recent, today)
    previous = prior_days[0] if prior_days else None
    system = prompts.SYSTEM_PROMPT
    user = prompts.build_user_prompt(
        signals, ph, upcoming, prior_editions=prior_days
    )

    raw, generator_label = _draft_raw(name, system, user, signals, ph, upcoming)

    # 5. Normalize + uniqueness. A clone of the most recent edition retries once,
    # then hard-fails — never publish yesterday's headline/dek/theEdge again.
    slate_record = phase_mod.current_record(schedule, ph)
    if ph.get("type") in ("regular", "postseason") and not slate_record:
        raise FactCheckError(
            "Chiefs Narrative has no current-season record from the schedule; "
            "refusing last-season fallback."
        )
    meta = {
        "generatedAt": config.iso_now(),
        "updatedAt": config.iso_now(),
        "generator": generator_label,
        "record": slate_record,
        "markets": signals.get("markets", {}),
    }
    narrative = _assemble_narrative(raw, ph, meta, signals, upcoming)
    if slate_record:
        narrative["record"] = slate_record
    _reuse_same_day_slug(narrative, recent)
    matched = _matched_copy_fields(narrative, previous)
    if matched:
        print(
            "  [writer] uniqueness: cloned "
            + ", ".join(matched)
            + " from the most recent edition; retrying once"
        )
        retry_user = user + "\n\n" + _uniqueness_retry_instruction(previous, matched)
        # If the LLM already failed and we fell back offline, don't burn another
        # full provider timeout on the retry — the offline writer is date-aware.
        retry_name = name if generator_label != "offline" else "offline"
        raw, generator_label = _draft_raw(
            retry_name, system, retry_user, signals, ph, upcoming
        )
        meta["generatedAt"] = config.iso_now()
        meta["updatedAt"] = config.iso_now()
        meta["generator"] = generator_label
        narrative = _assemble_narrative(raw, ph, meta, signals, upcoming)
        if slate_record:
            narrative["record"] = slate_record
        _reuse_same_day_slug(narrative, recent)
        matched = _matched_copy_fields(narrative, previous)
        if matched:
            slug = (previous or {}).get("slug") or ""
            raise DuplicateNarrativeError(
                "Chiefs Narrative uniqueness failed after retry: cloned "
                + ", ".join(matched)
                + (f" from {slug}" if slug else " from the most recent edition")
                + ". Refusing to publish a clone."
            )

    # 5b. ESPN fact-check on every generated section. Feed the exact
    # violations back for up to FACT_CHECK_RETRIES rewrites, then fail loud.
    recap = signals.get("lastGameRecap") or {}
    violations = facts.check_review(
        narrative, last, recap, schedule=schedule, copy_gates=True
    )
    attempt = 0
    while violations and attempt < FACT_CHECK_RETRIES:
        attempt += 1
        print(
            "  [writer] fact-check: "
            + "; ".join(violations)
            + f"; retrying {attempt}/{FACT_CHECK_RETRIES}"
        )
        retry_user = user + "\n\n" + facts.retry_instruction(violations, recap)
        retry_name = name if generator_label != "offline" else "offline"
        raw, generator_label = _draft_raw(
            retry_name, system, retry_user, signals, ph, upcoming
        )
        meta["generatedAt"] = config.iso_now()
        meta["updatedAt"] = config.iso_now()
        meta["generator"] = generator_label
        narrative = _assemble_narrative(raw, ph, meta, signals, upcoming)
        if slate_record:
            narrative["record"] = slate_record
        _reuse_same_day_slug(narrative, recent)
        matched = _matched_copy_fields(narrative, previous)
        if matched:
            slug = (previous or {}).get("slug") or ""
            raise DuplicateNarrativeError(
                "Chiefs Narrative uniqueness failed after fact-check retry: cloned "
                + ", ".join(matched)
                + (f" from {slug}" if slug else " from the most recent edition")
                + ". Refusing to publish a clone."
            )
        violations = facts.check_review(
            narrative, last, recap, schedule=schedule, copy_gates=True
        )
    dropped: list[str] = []
    if violations:
        print(
            "  [writer] fact-check still failing after retries; "
            "dropping offending sentences and re-checking the full edition"
        )

        def _drop_and_log(payload, problems):
            repaired = facts.repair_offending_copy(payload, problems, last)
            gone = facts.dropped_sentences(payload, repaired)
            for sentence in gone:
                print(f"  [writer] dropped sentence: {sentence}")
            leftover = facts.check_review(
                repaired, last, recap, schedule=schedule, copy_gates=True
            )
            return repaired, leftover, gone

        repaired, leftover, gone = _drop_and_log(narrative, violations)
        dropped.extend(gone)
        # A snippet drop can expose a newly bound leftover (run 36741379345
        # gave Miami's 34:21 to KC) or an orphan opener (run 36751657899).
        # Keep salvaging while a pass still removes copy.
        passes = 0
        while leftover and passes < 6:
            passes += 1
            repaired, leftover, gone = _drop_and_log(repaired, leftover)
            dropped.extend(gone)
            if not gone:
                break
        orphans = facts.check_repair_orphans(repaired, dropped)
        blockers = facts.repair_publish_blockers(
            leftover, repaired, orphans, before=narrative
        )
        if blockers:
            raise FactCheckError(blockers[0])
        narrative = repaired
        narrative["updatedAt"] = config.iso_now()
        print("  [writer] fact-check: dropped sentences logged; edition is clean")

    # 6. Render diagrams only after uniqueness and fact-check have passed.
    _render_diagrams(narrative)
    caption_issues = facts.check_diagram_captions(narrative)
    if caption_issues:
        raise FactCheckError(
            "Chiefs Narrative XO captions do not match why: "
            + "; ".join(caption_issues)
        )

    return {
        "narrative": narrative,
        "schedule": schedule,
        "news": signals.get("news") or [],
        "droppedSentences": dropped,
    }


def _write_edition_payload(narrative: dict) -> None:
    payload = json.dumps(narrative, indent=2, ensure_ascii=False) + "\n"
    config.NARRATIVE_JSON.write_text(payload, encoding="utf-8")
    slug = narrative.get("slug")
    if slug:
        config.ensure_dirs()
        (config.EDITIONS_DIR / f"{slug}.json").write_text(payload, encoding="utf-8")
    _purge_other_same_day_editions(narrative)


def render_published_diagrams(*, stamp: bool = False) -> dict:
    """Re-render public/images/narrative/<slug>/xo-*.svg from narrative.json."""
    narrative = json.loads(config.NARRATIVE_JSON.read_text(encoding="utf-8"))
    _render_diagrams(narrative)
    if stamp:
        narrative["updatedAt"] = config.iso_now()
        _write_edition_payload(narrative)
    issues = facts.check_diagram_captions(narrative)
    if issues:
        raise FactCheckError("; ".join(issues))
    return narrative


def _last_game_from_edition(narrative: dict) -> dict:
    review = narrative.get("lastGameReview") or {}
    score = review.get("score") or ""
    match = re.search(r"(\d+)\s*[–-]\s*(\d+)", score)
    kc_score = int(match.group(1)) if match else None
    opp_score = int(match.group(2)) if match else None
    return {
        "completed": bool(review.get("result") or score),
        "kcScore": kc_score,
        "oppScore": opp_score,
        "opponent": review.get("opponent") or "",
        "kickoff": review.get("label") or "",
        "date": (narrative.get("generatedAt") or "")[:10] + "T17:00:00Z",
    }


def _recap_for_edition(narrative: dict) -> dict:
    """Load the truth recap from this checkout's tools/ fixtures.

    Edition QA overlays only data/ + public/images/narrative from a PR.
    Fixtures stay on main so a fork cannot swap the ESPN recap.
    """
    review = narrative.get("lastGameReview") or {}
    opponent = (review.get("opponent") or "").lower()
    if "miami" in opponent:
        path = (
            config.REPO_ROOT
            / "tools"
            / "tests"
            / "fixtures"
            / "espn_401872952_recap.json"
        )
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def check_published_edition() -> list[str]:
    """Fact-check data/narrative.json plus visible XO captions."""
    narrative = json.loads(config.NARRATIVE_JSON.read_text(encoding="utf-8"))
    last = _last_game_from_edition(narrative)
    recap = _recap_for_edition(narrative)
    issues = facts.check_review(narrative, last, recap)
    issues.extend(facts.check_diagram_captions(narrative))
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate the Chiefs Narrative edition.")
    parser.add_argument("--provider", help="force provider (grok|openai|anthropic|claude-cli|codex-cli|offline)")
    parser.add_argument("--dry-run", action="store_true", help="print JSON, do not write files")
    parser.add_argument(
        "--schedule-only",
        action="store_true",
        help="refresh data/schedule_2026.json from ESPN and exit (no narrative)",
    )
    parser.add_argument(
        "--diagrams-only",
        action="store_true",
        help="re-render XO SVGs from data/narrative.json and exit",
    )
    parser.add_argument(
        "--stamp-updated",
        action="store_true",
        help="with --diagrams-only, write updatedAt on the in-place edition",
    )
    parser.add_argument(
        "--check-edition",
        action="store_true",
        help="run check_review and the SVG/why gate on data/narrative.json",
    )
    args = parser.parse_args(argv)

    if args.schedule_only:
        games = collect.refresh_schedule()
        print("-" * 52)
        print(f"  slate: {len(games)} games")
        return 0

    if args.diagrams_only:
        try:
            narrative = render_published_diagrams(stamp=args.stamp_updated)
        except FactCheckError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(f"  diagrams: {len(narrative.get('xsandos', []))}")
        return 0

    if args.check_edition:
        issues = check_published_edition()
        if issues:
            print("ERROR: published edition failed gates:", file=sys.stderr)
            for item in issues:
                print(f"  - {item}", file=sys.stderr)
            return 1
        print("  edition gates: ok")
        return 0

    try:
        result = build(args.provider, persist_schedule=not args.dry_run)
    except LiveGameSkip as exc:
        print(f"  [generate] {exc}")
        return 0
    except DuplicateNarrativeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except FactCheckError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    narrative = result["narrative"]

    if args.dry_run:
        print(json.dumps(narrative, indent=2, ensure_ascii=False))
        return 0

    config.ensure_dirs()
    drops = result.get("droppedSentences") or []
    config.REPAIR_JSON.write_text(
        json.dumps(
            {"droppedSentences": drops, "holdAutomerge": bool(drops)},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_edition_payload(narrative)
    _write_archive(narrative)
    _write_schedule(result["schedule"])
    _write_wire(result.get("news") or [])

    print("-" * 52)
    for path in (config.NARRATIVE_JSON, config.ARCHIVE_JSON):
        try:
            shown = path.relative_to(config.REPO_ROOT)
        except ValueError:
            shown = path
        print(f"  wrote {shown}")
    print(f"  edition: {narrative['edition']} — {narrative['headline']}")
    print(f"  generator: {narrative['generator']}")
    print(f"  diagrams: {len(narrative.get('xsandos', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
