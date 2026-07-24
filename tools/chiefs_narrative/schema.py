"""Normalize and validate a Chiefs Narrative object.

Both the LLM providers and the offline writer emit the same shape; this module
is the single gate that guarantees the Hugo template always receives every key
it expects, with sane types, regardless of who wrote it. It is deliberately
forgiving: missing/garbage fields are coerced to safe defaults rather than
raising, because a daily automation must not hard-fail on a flaky model reply.
"""
from __future__ import annotations

from . import config, diagrams


def _s(value, default="") -> str:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip()
    return str(value)


def _list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _str_list(value, limit=12) -> list[str]:
    out = []
    for item in _list(value):
        text = _s(item)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _obj_list(value, keys: dict, limit=12) -> list[dict]:
    """Coerce a list of dicts, keeping only ``keys`` with string defaults."""
    out = []
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        row = {k: _s(item.get(k), dflt) for k, dflt in keys.items()}
        if any(row.values()):
            out.append(row)
        if len(out) >= limit:
            break
    return out


def normalize(raw: dict, *, phase: dict, meta: dict) -> dict:
    """Return a fully-populated, validated narrative dict."""
    raw = raw or {}
    pg = phase or {}

    narrative = {
        "schemaVersion": config.SCHEMA_VERSION,
        "generatedAt": meta.get("generatedAt") or config.iso_now(),
        "generator": meta.get("generator", "offline"),
        "phase": {
            "type": _s(pg.get("type"), "offseason"),
            "label": _s(pg.get("label"), "Offseason"),
            "week": pg.get("week"),
            "mode": _s(pg.get("mode"), "offseason"),
        },
        "edition": _s(raw.get("edition") or pg.get("edition"), "Chiefs Narrative"),
        "record": _s(raw.get("record") or meta.get("record"), config.TEAM["last_season_record"]),
        "headline": _s(raw.get("headline"), "The Chiefs Narrative"),
        "dek": _s(raw.get("dek")),
        "videoHook": _s(raw.get("videoHook")),
        "theEdge": _s(raw.get("theEdge")),
        "storyline": _norm_storyline(raw.get("storyline")),
        "nextGame": _norm_next_game(raw.get("nextGame")),
        "markets": meta.get("markets") or {},
        "spotlight": _obj_list(
            raw.get("spotlight"),
            {"tag": "", "title": "", "body": ""},
            limit=8,
        ),
        "matchups": _obj_list(
            raw.get("matchups"),
            {"unit": "", "chiefs": "", "opponent": "", "edge": "PUSH", "note": ""},
            limit=8,
        ),
        "xsandos": _norm_xsandos(raw.get("xsandos")),
        "coaching": _obj_list(
            raw.get("coaching"), {"topic": "", "detail": ""}, limit=6
        ),
        "injuries": _obj_list(
            raw.get("injuries"),
            {"player": "", "status": "", "note": "", "source": ""},
            limit=10,
        ),
        "personnel": _obj_list(
            raw.get("personnel"), {"move": "", "detail": ""}, limit=10
        ),
        "strategies": _str_list(raw.get("strategies"), limit=8),
        "debates": _str_list(raw.get("debates"), limit=8),
        "runOfShow": _obj_list(
            raw.get("runOfShow"),
            {"segment": "", "length": "", "talkTrack": ""},
            limit=10,
        ),
        "sources": _obj_list(
            raw.get("sources"),
            {"title": "", "publisher": "", "url": "", "note": ""},
            limit=16,
        ),
    }

    # Normalize matchup edge to a known token.
    for m in narrative["matchups"]:
        edge = m["edge"].upper()
        m["edge"] = edge if edge in ("KC", "OPP", "PUSH") else "PUSH"

    return narrative


def _norm_storyline(value) -> dict:
    if isinstance(value, dict):
        return {
            "lede": _s(value.get("lede")),
            "body": _str_list(value.get("body"), limit=8),
        }
    if isinstance(value, str):
        return {"lede": _s(value), "body": []}
    return {"lede": "", "body": []}


def _norm_next_game(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "label": _s(value.get("label")),
        "opponent": _s(value.get("opponent")),
        "at": _s(value.get("at")),
        "tv": _s(value.get("tv")),
        "note": _s(value.get("note")),
    }


def _norm_xsandos(value) -> list[dict]:
    """Validate X&O entries and clamp the concept to a known one."""
    out = []
    valid = set(diagrams.concept_keys())
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        concept = _s(item.get("concept"))
        if concept not in valid:
            concept = diagrams.DEFAULT_CONCEPT
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        out.append(
            {
                "title": _s(item.get("title")) or diagrams.CONCEPTS[concept]["title"],
                "situation": _s(item.get("situation")),
                "concept": concept,
                "why": _s(item.get("why")),
                "coaching": _s(item.get("coaching")),
                "labels": {str(k): _s(v) for k, v in labels.items()},
                # diagram file + side filled in during rendering
            }
        )
        if len(out) >= 4:
            break
    return out
