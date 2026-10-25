"""Normalize and validate a Chiefs Narrative object.

Both the LLM providers and the offline writer emit the same shape; this module
is the single gate that guarantees the Hugo template always receives every key
it expects, with sane types, regardless of who wrote it. It is deliberately
forgiving: missing/garbage fields are coerced to safe defaults rather than
raising, because a daily automation must not hard-fail on a flaky model reply.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from . import config, diagrams, phase as phase_mod

# Official X/Twitter status URLs only. Media is never downloaded or rehosted —
# the site renders publish.twitter.com oEmbed / widgets.js blockquotes.
_X_STATUS_RE = re.compile(
    r"^https://(?:www\.)?(?:twitter|x)\.com/([A-Za-z0-9_]+)/status/(\d+)/?(?:\?.*)?$",
    re.IGNORECASE,
)
_X_RESERVED_HANDLES = frozenset({"i", "intent", "share", "home", "explore", "search"})
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_URL_RE = re.compile(
    r"^https://(?:www\.)?(?:youtube\.com/(?:watch\?v=|embed/|shorts/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})(?:[?&/#].*)?$",
    re.IGNORECASE,
)


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


def _norm_x_embed(value) -> dict | None:
    """Keep an official X status URL + visible account credit, or drop it.

    Accepts a URL string or a dict with url/label (and snake_case aliases).
    Account credit always comes from the status URL handle. Invented or
    non-status URLs are discarded so the template never renders an empty box.
    """
    if isinstance(value, str):
        url, label = _s(value), ""
    elif isinstance(value, dict):
        url = _s(
            value.get("url")
            or value.get("embedUrl")
            or value.get("embed_url")
        )
        label = _s(value.get("label") or value.get("caption") or value.get("player"))
    else:
        return None
    if not url:
        return None
    match = _X_STATUS_RE.match(url.split("#", 1)[0])
    if not match:
        return None
    handle, status_id = match.group(1), match.group(2)
    if handle.lower() in _X_RESERVED_HANDLES:
        return None
    if not status_id.isdigit():
        return None
    # Credit the handle from the status URL. A writer-supplied account is
    # ignored so a mismatched label cannot attach someone else's post.
    embed = {
        "url": f"https://x.com/{handle}/status/{status_id}",
        "account": f"@{handle}",
    }
    if label:
        embed["label"] = label
    return embed


def _norm_x_embeds(value, limit=2) -> list[dict]:
    out = []
    for item in _list(value):
        embed = _norm_x_embed(item)
        if embed:
            out.append(embed)
        if len(out) >= limit:
            break
    return out


def _norm_press_conference(value) -> dict | None:
    """Keep a YouTube watch URL with a named speaker, or drop it.

    Accepts a URL string or a dict. Invented hosts and speaker-less rows
    are discarded so the template never renders an empty iframe.
    """
    if isinstance(value, str):
        url, speaker, title, channel, date, quote, date_label = (
            _s(value),
            "",
            "",
            "",
            "",
            "",
            "",
        )
    elif isinstance(value, dict):
        url = _s(
            value.get("url")
            or value.get("embedUrl")
            or value.get("embed_url")
            or value.get("youtubeUrl")
        )
        speaker = _s(
            value.get("speaker")
            or value.get("speakers")
            or value.get("name")
            or value.get("label")
        )
        title = _s(value.get("title") or value.get("caption"))
        channel = _s(value.get("channel") or value.get("author") or value.get("account"))
        date = _s(value.get("date") or value.get("sessionDate") or value.get("published"))
        quote = _s(value.get("quote"))
        date_label = _s(value.get("dateLabel") or value.get("when"))
        video_id = _s(value.get("videoId") or value.get("video_id"))
        if not url and _YT_ID_RE.match(video_id):
            url = f"https://www.youtube.com/watch?v={video_id}"
    else:
        return None
    if not url:
        return None
    raw = url.split("#", 1)[0]
    match = _YT_URL_RE.match(raw)
    video_id = match.group(1) if match else ""
    if not video_id:
        parsed = urlparse(raw)
        host = (parsed.netloc or "").lower()
        if "youtu.be" in host:
            video_id = parsed.path.strip("/").split("/")[0]
        elif "youtube.com" in host and parsed.path.startswith("/watch"):
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
        elif "youtube.com" in host and parsed.path.startswith(
            ("/embed/", "/shorts/", "/live/")
        ):
            parts = parsed.path.strip("/").split("/")
            video_id = parts[1] if len(parts) > 1 else ""
    if not _YT_ID_RE.match(video_id or ""):
        return None
    if not speaker:
        return None
    if date and "T" in date:
        date = date[:10]
    embed = {
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "videoId": video_id,
        "speaker": speaker,
        "channel": channel or "YouTube",
        "title": title,
        "date": date,
    }
    if date_label:
        embed["dateLabel"] = date_label
    if quote:
        embed["quote"] = quote
    return embed


def _norm_press_conferences(value, limit=8) -> list[dict]:
    out = []
    for item in _list(value):
        embed = _norm_press_conference(item)
        if embed:
            out.append(embed)
        if len(out) >= limit:
            break
    return out


def _norm_retweeted_by(value) -> dict | None:
    """Keep a named status URL for a retweet of the locker-room post."""
    if not isinstance(value, dict):
        return None
    handle = _s(value.get("handle") or value.get("account")).lstrip("@")
    status_id = _s(value.get("statusId") or value.get("status_id") or value.get("id"))
    url = _s(value.get("url"))
    if url:
        embed = _norm_x_embed(url)
        if not embed:
            return None
        url = embed["url"]
        handle = handle or embed["account"].lstrip("@")
        status_id = status_id or url.rsplit("/", 1)[-1]
    if not handle or not status_id.isdigit():
        return None
    if not url:
        url = f"https://x.com/{handle}/status/{status_id}"
        embed = _norm_x_embed(url)
        if not embed:
            return None
        url = embed["url"]
    return {"handle": f"@{handle}", "statusId": status_id, "url": url}


def _norm_locker_post(value) -> dict | None:
    """Keep a real X status URL with a named player/coach handle, or drop it."""
    if isinstance(value, str):
        value = {"url": value}
    if not isinstance(value, dict):
        return None
    base = _norm_x_embed(value)
    if not base:
        return None
    handle = _s(base.get("account")).lstrip("@")
    if not handle:
        return None
    date = _s(value.get("date") or value.get("posted") or value.get("published"))
    if date and "T" in date:
        date = date[:10]
    role = _s(value.get("role") or "player").lower() or "player"
    if role not in ("player", "coach"):
        role = "player"
    row = {
        "url": base["url"],
        "handle": f"@{handle}",
        "name": _s(value.get("name") or value.get("speaker") or handle),
        "team": _s(value.get("team")).upper(),
        "role": role,
        "date": date,
        "quote": _s(value.get("quote") or value.get("text")),
    }
    date_label = _s(value.get("dateLabel") or value.get("when"))
    if date_label:
        row["dateLabel"] = date_label
    if value.get("highlight") or value.get("special"):
        row["highlight"] = True
    note = _s(value.get("highlightNote") or value.get("note"))
    if note:
        row["highlightNote"] = note
    retweeted = _norm_retweeted_by(value.get("retweetedBy") or value.get("retweeted_by"))
    if retweeted:
        row["retweetedBy"] = retweeted
    return row


def _norm_locker_room(value, limit=6) -> dict:
    """Always a {posts: [...]} object so Hugo can render the empty state."""
    if isinstance(value, dict) and not (
        value.get("url") or value.get("embedUrl") or value.get("embed_url")
    ):
        items = value.get("posts") or value.get("lockerRoom")
    else:
        items = value
    posts = []
    for item in _list(items):
        row = _norm_locker_post(item)
        if row:
            posts.append(row)
        if len(posts) >= limit:
            break
    return {"posts": posts}


def _norm_analysis(value, key_play_embeds=None) -> list[dict]:
    """Film-room paragraphs, optionally paired with an official X play embed.

    Strings stay valid (legacy editions). Objects may carry ``embed`` or a
    parallel ``keyPlayEmbeds`` list aligned by index. Paragraphs without a
    real status URL keep only ``body`` so Hugo renders a normal ``<p>``.
    """
    extras = _list(key_play_embeds)
    out = []
    for index, item in enumerate(_list(value)):
        embed = None
        if isinstance(item, str):
            body = _s(item)
        elif isinstance(item, dict):
            body = _s(item.get("body") or item.get("text") or item.get("paragraph"))
            embed = _norm_x_embed(
                item.get("embed")
                or item.get("embedUrl")
                or item.get("embed_url")
            )
        else:
            continue
        if not body:
            continue
        if embed is None and index < len(extras):
            embed = _norm_x_embed(extras[index])
        row = {"body": body}
        if embed:
            row["embed"] = embed
        out.append(row)
        if len(out) >= 6:
            break
    return out


def normalize(raw: dict, *, phase: dict, meta: dict) -> dict:
    """Return a fully-populated, validated narrative dict."""
    raw = raw or {}
    pg = phase or {}

    narrative = {
        "schemaVersion": config.SCHEMA_VERSION,
        "generatedAt": meta.get("generatedAt") or config.iso_now(),
        "updatedAt": meta.get("updatedAt") or raw.get("updatedAt") or "",
        "generator": meta.get("generator", "offline"),
        "phase": {
            "type": _s(pg.get("type"), "offseason"),
            "label": _s(pg.get("label"), "Offseason"),
            "week": pg.get("week"),
            "mode": _s(pg.get("mode"), "offseason"),
        },
        "edition": _s(phase_mod.format_edition(pg) or raw.get("edition"), "Chiefs Narrative"),
        "record": _s(raw.get("record") or meta.get("record")),
        "headline": _s(raw.get("headline"), "The Chiefs Narrative"),
        "sectionTitles": {
            key: _s((raw.get("sectionTitles") or {}).get(key))
            for key in ("lastGame", "currentState", "gamePlan", "storyline", "pressConferences", "xsandos", "injuries", "personnel")
            if isinstance(raw.get("sectionTitles"), dict) and _s(raw["sectionTitles"].get(key))
        },
        "dek": _s(raw.get("dek")),
        "videoHook": _s(raw.get("videoHook")),
        "theEdge": _s(raw.get("theEdge")),
        "playerEmbeds": _norm_x_embeds(
            raw.get("playerEmbeds") or raw.get("player_embeds"),
            limit=2,
        ),
        "pressConferences": _norm_press_conferences(
            raw.get("pressConferences") or raw.get("press_conferences"),
            limit=8,
        ),
        "pressConferenceLinks": [
            item for item in _obj_list(raw.get("pressConferenceLinks"),
                {"url":"", "speaker":"", "title":"", "date":"", "dateLabel":"", "image":""}, limit=8)
            if item["url"].startswith("https://www.chiefs.com/video/") and item["speaker"]
            and item["image"].startswith("https://static.clubs.nfl.com/image/")
        ],
        "lockerRoom": _norm_locker_room(
            raw.get("lockerRoom") or raw.get("locker_room"),
            limit=6,
        ),
        "storyline": _norm_storyline(raw.get("storyline")),
        "lastGameReview": _norm_last_game_review(raw.get("lastGameReview")),
        "currentState": _norm_current_state(raw.get("currentState")),
        "gamePlan": _norm_game_plan(raw.get("gamePlan")),
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
            {"player": "", "status": "", "note": "", "source": "", "image": "", "imageSource": ""},
            limit=10,
        ),
        "personnel": _obj_list(
            raw.get("personnel"), {"move": "", "detail": ""}, limit=10
        ),
        "strategies": _str_list(raw.get("strategies"), limit=8),
        "debates": _str_list(raw.get("debates"), limit=8),
        # Legacy/internal data remains readable; the public report never
        # renders a script and the current writer prompt requests no script.
        "runOfShow": _obj_list(raw.get("runOfShow"),
            {"segment":"", "length":"", "talkTrack":""}, limit=10),
        "sources": _obj_list(
            raw.get("sources"),
            {"title": "", "publisher": "", "url": "", "note": ""},
            limit=16,
        ),
    }

    # Portraits are optional official profile references, never generated faces.
    for item in narrative["injuries"]:
        if not (item["image"].startswith("https://static.clubs.nfl.com/image/")
                and item["imageSource"].startswith("https://www.chiefs.com/team/players-roster/")):
            item["image"] = ""
            item["imageSource"] = ""

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


def _norm_last_game_review(value) -> dict:
    if not isinstance(value, dict):
        return {}
    review = {
        "opponent": _s(value.get("opponent")),
        "label": _s(value.get("label")),
        "result": _s(value.get("result")).upper(),
        "score": _s(value.get("score")),
        "lede": _s(value.get("lede")),
        "analysis": _norm_analysis(
            value.get("analysis"),
            value.get("keyPlayEmbeds") or value.get("key_play_embeds"),
        ),
        "takeaways": _obj_list(
            value.get("takeaways"), {"title": "", "body": ""}, limit=6
        ),
        "whatWorked": _str_list(value.get("whatWorked"), limit=6),
        "whatDidnt": _str_list(value.get("whatDidnt"), limit=6),
    }
    if review["result"] and review["result"] not in ("W", "L", "T"):
        review["result"] = ""
    if not any(
        review[k]
        for k in ("lede", "analysis", "takeaways", "whatWorked", "whatDidnt", "score")
    ):
        return {}
    return review


def _norm_current_state(value) -> dict:
    if not isinstance(value, dict):
        return {}
    state = {
        "lede": _s(value.get("lede")),
        "record": _s(value.get("record")),
        "workOn": _obj_list(value.get("workOn"), {"title": "", "body": ""}, limit=6),
        "thinkAbout": _obj_list(
            value.get("thinkAbout"), {"title": "", "body": ""}, limit=6
        ),
    }
    if not any(state[k] for k in ("lede", "workOn", "thinkAbout", "record")):
        return {}
    return state


def _norm_game_plan(value) -> dict:
    if not isinstance(value, dict):
        return {}
    plan = {
        "opponent": _s(value.get("opponent")),
        "lede": _s(value.get("lede")),
        "howTheyMatch": _s(value.get("howTheyMatch")),
        "keys": _obj_list(value.get("keys"), {"title": "", "body": ""}, limit=6),
        "script": _str_list(value.get("script"), limit=6),
    }
    if not any(plan[k] for k in ("lede", "howTheyMatch", "keys", "script")):
        return {}
    return plan


def _norm_next_game(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {
        "id": _s(value.get("id")),
        "label": _s(value.get("label")),
        "opponent": _s(value.get("opponent")),
        "at": _s(value.get("at")),
        "tv": _s(value.get("tv")),
        "note": _s(value.get("note")),
    }


def _norm_xsandos(value) -> list[dict]:
    """Validate X&O entries and drop empty placeholder cards.

    A card with no why and no situation is not a real teaching beat. Do not
    keep it by falling back to DEFAULT_CONCEPT — generate.py tops up with
    real offense/defense cards instead.
    """
    out = []
    valid = set(diagrams.concept_keys())
    for item in _list(value):
        if not isinstance(item, dict):
            continue
        why = _s(item.get("why"))
        situation = _s(item.get("situation"))
        if not why and not situation:
            continue
        concept = _s(item.get("concept"))
        if concept not in valid:
            concept = diagrams.DEFAULT_CONCEPT
        labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
        out.append(
            {
                "title": _s(item.get("title")) or diagrams.CONCEPTS[concept]["title"],
                "situation": situation,
                "concept": concept,
                "why": why,
                "coaching": _s(item.get("coaching")),
                "labels": {str(k): _s(v) for k, v in labels.items()},
                # diagram file + side filled in during rendering
            }
        )
        if len(out) >= 6:
            break
    return out
