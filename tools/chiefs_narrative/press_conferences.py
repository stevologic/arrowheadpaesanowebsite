"""Collect and verify YouTube press-conference embeds for Preview/Recap.

Schema normalize only checks URL shape and a named speaker. Generation
must not publish a hallucinated video ID: every embed is kept only when
YouTube oEmbed returns 200, the channel is allowlisted, the clip is from
this game week, and the speaker field names who the video actually shows.
Dead or unverified published embeds fail the edition gate — they are not
stripped the way unofficial X posts are. Collected candidates that the
verifier would reject — including today's identity-less livestream
placeholders — are omitted so one unverifiable clip cannot abort the
edition. Future-dated oEmbed 200s with no channel identity are dropped
the same way. Nothing unverified is written into the published list.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import requests

from . import config, phase as phase_mod, schema

OEMBED_ENDPOINT = "https://www.youtube.com/oembed"
OEMBED_TIMEOUT_SEC = 8
_CT = ZoneInfo("America/Chicago")
_MISSING = object()

# Official + clearly labeled news-desk channels. Compared lowercase
# without a leading @. Fan edits and clip channels are not accepted.
OFFICIAL_YT_AUTHORS = frozenset(
    {
        "kansas city chiefs",
        "nfl",
        "espn",
        "espn nfl",
        "nfl network",
        "arrowhead pride",
        "kctv 5",
        "kmbc 9",
        "fox4 kansas city",
        "fox 4 kansas city",
    }
)
OFFICIAL_YT_HANDLES = frozenset(
    {
        "kansascitychiefs",
        "nfl",
        "espn",
        "espnnfl",
        "nflnetwork",
        "arrowheadpride",
        "kctv5",
        "kmbc9",
        "fox4kc",
    }
)

YOUTUBE_FEEDS = (
    {
        "channel": "Kansas City Chiefs",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UC-hXefb6XBFSubWz6Ezf_lA",
    },
    {
        "channel": "Kansas City Chiefs",
        "url": "https://www.youtube.com/feeds/videos.xml?playlist_id=PL-9iO2rIrpm7b46_3CrRMFsPFveerS_bT",
    },
    {
        "channel": "NFL",
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCDVYQ4Zhbm3S2dlz7P1GBDg",
    },
)

_PRESS_HINT = re.compile(
    r"press conference|speaks? to (?:the )?media|speak to media|postgame press",
    re.IGNORECASE,
)
_TITLE_DATE = re.compile(
    r"\b("
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december"
    r")\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?\b",
    re.IGNORECASE,
)
_TITLE_MD = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
_KNOWN_SPEAKERS = (
    "Andy Reid",
    "Patrick Mahomes",
    "Steve Spagnuolo",
    "Eric Bieniemy",
    "Dave Toub",
    "Matt Nagy",
    "Kenneth Walker III",
    "Ken Walker",
    "Nick Bolton",
    "Chris Jones",
    "Travis Kelce",
    "Xavier Worthy",
    "Rashee Rice",
    "Creed Humphrey",
    "Harrison Butker",
    "George Karlaftis",
    "Drue Tranquill",
    "Alohi Gilman",
    "Jack Cochrane",
    "Noah Gray",
    "Trey Smith",
    "Joe Cullen",
    "Dave Merritt",
    "Matt Araiza",
)


def video_id_from_url(url: str) -> str:
    """11-character YouTube ID from a watch, shorts, embed, or youtu.be URL."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    path = parsed.path or ""
    if "youtu.be" in host:
        return path.strip("/").split("/")[0][:11]
    if "youtube" not in host:
        return ""
    if path.startswith("/embed/") or path.startswith("/shorts/") or path.startswith("/live/"):
        return path.strip("/").split("/")[1][:11] if "/" in path.strip("/") else ""
    if path.startswith("/watch"):
        return (parse_qs(parsed.query).get("v") or [""])[0][:11]
    return ""


def _http_get(url: str) -> str | None:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT, "Accept": "*/*"},
            timeout=config.HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"  [collect] warning: could not fetch {url}: {exc}")
        return None


def handle_from_author_url(author_url: str) -> str:
    """Handle from oEmbed author_url only — never author_name."""
    path = urlparse(author_url or "").path.strip("/")
    handle = path.split("/")[0] if path else ""
    return handle.lstrip("@")


def fetch_oembed(url: str, *, timeout: float = OEMBED_TIMEOUT_SEC) -> dict | None:
    """Return the oEmbed JSON object or None. Never raises to callers."""
    try:
        resp = requests.get(
            OEMBED_ENDPOINT,
            params={"url": url, "format": "json"},
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    try:
        payload = resp.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def author_is_allowlisted(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    name = str(payload.get("author_name") or "").strip().lower()
    if name in OFFICIAL_YT_AUTHORS:
        return True
    handle = handle_from_author_url(payload.get("author_url") or "").lower()
    return handle in OFFICIAL_YT_HANDLES


def speakers_from_title(title: str) -> str:
    """Name the people the title actually lists. Do not invent a speaker."""
    found: list[str] = []
    blob = title or ""
    for name in _KNOWN_SPEAKERS:
        if name.lower() == "ken walker" and "kenneth walker" in " ".join(found).lower():
            continue
        if re.search(rf"\b{re.escape(name)}\b", blob, re.IGNORECASE):
            found.append(name)
    if found:
        if len(found) == 1:
            return found[0]
        if len(found) == 2:
            return f"{found[0]} and {found[1]}"
        return ", ".join(found[:-1]) + f", and {found[-1]}"
    return ""


def _now() -> datetime:
    return datetime.now(_CT)


def session_is_future(session: datetime | None, now: datetime | None = None) -> bool:
    """True when the session's America/Chicago calendar day is after today."""
    if session is None:
        return False
    now_ct = (now or _now()).astimezone(_CT)
    return session.astimezone(_CT).date() > now_ct.date()


def upcoming_placeholder(payload: dict | None) -> bool:
    """oEmbed 200 with no channel identity — typical of scheduled livestreams."""
    if not isinstance(payload, dict):
        return False
    name = str(payload.get("author_name") or "").strip()
    url = str(payload.get("author_url") or "").strip()
    return not name and not url


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def session_date_from_title(title: str, published: datetime | None) -> datetime | None:
    """Prefer the date printed on the video over the upload stamp."""
    match = _TITLE_DATE.search(title or "")
    if match:
        month, day, year = match.group(1), int(match.group(2)), match.group(3)
        year_i = int(year) if year else (published.astimezone(_CT).year if published else None)
        if year_i:
            try:
                return datetime.strptime(f"{month} {day} {year_i}", "%B %d %Y").replace(
                    tzinfo=_CT
                )
            except ValueError:
                pass
    match = _TITLE_MD.search(title or "")
    if match:
        month, day, year = int(match.group(1)), int(match.group(2)), match.group(3)
        if year:
            year_i = int(year)
            if year_i < 100:
                year_i += 2000
        elif published:
            year_i = published.astimezone(_CT).year
        else:
            year_i = None
        if year_i:
            try:
                return datetime(year_i, month, day, tzinfo=_CT)
            except ValueError:
                pass
    return published.astimezone(_CT) if published else None


def game_week_window(phase: dict | None) -> tuple[datetime | None, datetime | None]:
    """From last kickoff through the next kickoff (postgame belongs to this week)."""
    phase = phase or {}
    last = _parse_dt((phase.get("lastGame") or {}).get("date"))
    nxt = _parse_dt((phase.get("nextGame") or {}).get("date"))
    start = last if last else None
    end = nxt if nxt else None
    return start, end


def in_game_week(session: datetime | None, phase: dict | None) -> bool:
    """True when the session's America/Chicago date is this game week.

    Postgame on the last-game calendar day counts. The next kickoff's
    calendar day is included only before that kickoff.
    """
    if session is None:
        return False
    start, end = game_week_window(phase)
    session_ct = session.astimezone(_CT)
    if start:
        last_day = start.astimezone(_CT).date()
        if session_ct.date() < last_day:
            return False
    if end:
        next_ct = end.astimezone(_CT)
        if session_ct.date() > next_ct.date():
            return False
        if session_ct.date() == next_ct.date() and session_ct >= next_ct:
            return False
    return True


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _parse_youtube_feed(xml_text: str, fallback_channel: str) -> list[dict]:
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for node in root.iter():
        if _strip_ns(node.tag) != "entry":
            continue
        fields: dict[str, str] = {}
        video_id = ""
        link = ""
        for child in node:
            name = _strip_ns(child.tag)
            if name == "videoId":
                video_id = (child.text or "").strip()
            elif name == "title":
                fields["title"] = (child.text or "").strip()
            elif name == "published":
                fields["published"] = (child.text or "").strip()
            elif name == "link":
                link = child.get("href") or (child.text or "").strip() or link
            elif name == "name":
                fields.setdefault("author", (child.text or "").strip())
        title = fields.get("title") or ""
        if not video_id:
            video_id = video_id_from_url(link)
        if not video_id or not title:
            continue
        published = _parse_dt(fields.get("published"))
        items.append(
            {
                "videoId": video_id,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "channel": fields.get("author") or fallback_channel,
                "published": published.isoformat() if published else "",
                "_published": published,
            }
        )
    return items


def _session_from_item(item) -> datetime | None:
    if not isinstance(item, dict):
        return None
    raw_date = item.get("date")
    if raw_date:
        try:
            return datetime.strptime(str(raw_date)[:10], "%Y-%m-%d").replace(
                tzinfo=_CT
            )
        except ValueError:
            pass
    return session_date_from_title(
        item.get("title") or "", _parse_dt(item.get("published"))
    )


def _candidate_in_week(item, phase: dict | None) -> bool:
    session = _session_from_item(item)
    if session is None:
        return True
    return in_game_week(session, phase)


def _candidate_not_ready(item, payload: dict | None) -> bool:
    """Skip only future-dated oEmbed 200s that have no channel identity.

    A past-dated or undated identity-less payload is unverified, not a
    livestream placeholder. Future sessions that already have an author
    still go through verify and fail closed.
    """
    return upcoming_placeholder(payload) and session_is_future(
        _session_from_item(item)
    )


def collect_candidates(phase: dict | None = None) -> list[dict]:
    """Pull official-channel RSS items that look like this week's pressers."""
    seen: set[str] = set()
    out: list[dict] = []
    for feed in YOUTUBE_FEEDS:
        xml_text = _http_get(feed["url"])
        if not xml_text:
            continue
        for item in _parse_youtube_feed(xml_text, feed["channel"]):
            vid = item["videoId"]
            if vid in seen:
                continue
            if not _PRESS_HINT.search(item["title"]):
                continue
            session = session_date_from_title(item["title"], item.get("_published"))
            if session_is_future(session):
                print(f"  [collect] skip future presser {item['url']}")
                continue
            if phase and not in_game_week(session, phase):
                continue
            speaker = speakers_from_title(item["title"])
            if not speaker:
                continue
            seen.add(vid)
            row = {
                "url": item["url"],
                "videoId": vid,
                "speaker": speaker,
                "channel": item["channel"],
                "title": item["title"],
                "date": session.date().isoformat() if session else "",
            }
            if session:
                row["dateLabel"] = (
                    f"{session.strftime('%a %b')} {session.day}, {session.year}"
                )
            out.append(row)
    out.sort(key=lambda row: row.get("date") or "", reverse=True)
    return out


def verify_embed(
    value, *, oembed_fetch=None, phase: dict | None = None, payload=_MISSING
) -> dict | None:
    """Keep an embed only when oEmbed 200s, the author is allowlisted, and it fits."""
    embed = schema._norm_press_conference(value)
    if not embed:
        return None
    if payload is _MISSING:
        fetch = fetch_oembed if oembed_fetch is None else oembed_fetch
        try:
            payload = fetch(embed["url"])
        except Exception:  # noqa: BLE001 — unverified is a failure, not a guess
            return None
    if not payload or not author_is_allowlisted(payload):
        return None
    oembed_title = str(payload.get("title") or "").strip()
    if oembed_title:
        embed["title"] = oembed_title
        named = speakers_from_title(oembed_title)
        if named:
            embed["speaker"] = named
    author = str(payload.get("author_name") or "").strip()
    if author:
        embed["channel"] = author
    if not embed.get("speaker"):
        return None
    title_l = (embed.get("title") or "").lower()
    first = embed["speaker"].split(",")[0].split(" and ")[0].strip().lower()
    if first and first not in title_l:
        return None
    session = _session_from_item(embed)
    if session_is_future(session):
        return None
    if phase and not in_game_week(session, phase):
        return None
    return embed


def _item_url(item) -> str:
    if isinstance(item, dict):
        return str(item.get("url") or item.get("videoId") or "").strip()
    if isinstance(item, str):
        return item.strip()
    return ""


def verify_all(
    items, *, oembed_fetch=None, phase: dict | None = None
) -> tuple[list[dict], list[str]]:
    """Return (kept, omitted). Unverified collected candidates are dropped, not fatal."""
    kept: list[dict] = []
    omitted: list[str] = []
    fetch = fetch_oembed if oembed_fetch is None else oembed_fetch
    for item in items or []:
        embed = schema._norm_press_conference(item)
        payload = _MISSING
        if embed:
            try:
                payload = fetch(embed["url"])
            except Exception:  # noqa: BLE001 — unverified is omitted, not guessed
                payload = None
        fetched = None if payload is _MISSING else payload
        if _candidate_not_ready(item, fetched):
            print(f"  [press] skip future placeholder {_item_url(item)}")
            continue
        verified = verify_embed(
            item, oembed_fetch=oembed_fetch, phase=phase, payload=payload
        )
        if verified:
            if isinstance(item, dict) and item.get("quote") and not verified.get("quote"):
                verified["quote"] = str(item["quote"]).strip()
            kept.append(verified)
            continue
        note = f"press conference: unverified embed {_item_url(item)}".strip()
        print(f"  [press] omit {note}")
        omitted.append(note)
    return kept, omitted


def attach_verified(
    narrative: dict,
    signals: dict | None,
    phase: dict | None,
    *,
    oembed_fetch=None,
) -> dict:
    """Overwrite writer-invented clips with collected, oEmbed-verified pressers.

    Preview and Recap (review) editions always get the field. Collected
    candidates that fail verification — future or today identity-less
    placeholders, past-dated identity-less 200s, dead URLs — are omitted
    so one unverifiable clip cannot abort the edition. check_embeds still
    fail-closes on anything that remains in the published list.
    """
    if not isinstance(narrative, dict):
        return narrative
    mode = str((phase or {}).get("mode") or "")
    if mode not in ("preview", "review"):
        narrative["pressConferences"] = []
        return narrative
    collected = [
        item
        for item in list((signals or {}).get("pressConferences") or [])
        if _candidate_in_week(item, phase)
    ]
    kept, _omitted = verify_all(collected, oembed_fetch=oembed_fetch, phase=phase)
    narrative["pressConferences"] = kept
    return narrative


def check_embeds(
    narrative: dict | None,
    *,
    oembed_fetch=None,
    schedule=None,
    phase: dict | None = None,
) -> list[str]:
    """Fail closed on every published press-conference embed that does not verify."""
    items = (narrative or {}).get("pressConferences")
    if not items:
        return []
    if not isinstance(items, list):
        return ["press conference: pressConferences must be a list"]
    if phase is None and schedule:
        phase = phase_mod.detect(schedule)
    issues: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("url"):
            issues.append("press conference: embed is missing a YouTube URL")
            continue
        if not str(item.get("speaker") or "").strip():
            issues.append(
                f"press conference: embed {item.get('url')} is missing the speaker it shows"
            )
        verified = verify_embed(item, oembed_fetch=oembed_fetch, phase=phase)
        if not verified:
            issues.append(f"press conference: unverified embed {item.get('url')}")
    return issues


class UnverifiedPressConferenceError(RuntimeError):
    """A collected or published press-conference embed failed oEmbed verification."""
