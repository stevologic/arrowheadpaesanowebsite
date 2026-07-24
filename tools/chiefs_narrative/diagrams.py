"""Deterministic X's-and-O's field-diagram renderer.

The writer never hand-authors SVG coordinates (that is fragile and hallucinates
overlapping players). Instead it picks a **concept** from :data:`CONCEPTS` and
supplies only labels/notes. Each concept is a self-contained function that
returns a complete, clean field SVG in the Arrowhead Paesano palette.

Add a concept by writing a ``_build_<key>()`` that returns an SVG string and
registering it in :data:`CONCEPTS` with a human title + teaching blurb.
"""
from __future__ import annotations

from pathlib import Path

# Canvas / palette -----------------------------------------------------------
W, H = 660, 470
LOS = 292                # line of scrimmage y
FIELD_TOP, FIELD_BOTTOM = 40, 430
FIELD_LEFT, FIELD_RIGHT = 40, 620

RED = "#e31837"
NAVY = "#0b2340"
GOLD = "#ffb81c"
INK = "#0d0c0d"
GRASS_A = "#123f27"
GRASS_B = "#0e3421"


# --- low-level drawing helpers ---------------------------------------------
def _field() -> list[str]:
    """Background grass, yard lines, hashes, and the line of scrimmage."""
    parts = [
        f'<rect x="0" y="0" width="{W}" height="{H}" rx="18" fill="{GRASS_A}"/>',
    ]
    # Alternating 5-yard bands (every 30px ~ 5 yards).
    band = 30
    y = FIELD_TOP
    toggle = False
    while y < FIELD_BOTTOM:
        if toggle:
            parts.append(
                f'<rect x="{FIELD_LEFT}" y="{y}" width="{FIELD_RIGHT-FIELD_LEFT}" '
                f'height="{min(band, FIELD_BOTTOM-y)}" fill="{GRASS_B}"/>'
            )
        toggle = not toggle
        y += band
    # Yard lines.
    y = FIELD_TOP
    while y <= FIELD_BOTTOM:
        parts.append(
            f'<line x1="{FIELD_LEFT}" y1="{y}" x2="{FIELD_RIGHT}" y2="{y}" '
            f'stroke="rgba(255,255,255,.18)" stroke-width="1.5"/>'
        )
        y += band
    # Hash marks down the middle third.
    for hx in (FIELD_LEFT + (FIELD_RIGHT - FIELD_LEFT) * 0.38,
               FIELD_LEFT + (FIELD_RIGHT - FIELD_LEFT) * 0.62):
        yy = FIELD_TOP + 8
        while yy <= FIELD_BOTTOM - 6:
            parts.append(
                f'<line x1="{hx:.0f}" y1="{yy}" x2="{hx:.0f}" y2="{yy+5}" '
                f'stroke="rgba(255,255,255,.22)" stroke-width="1.4"/>'
            )
            yy += band
    # Line of scrimmage.
    parts.append(
        f'<line x1="{FIELD_LEFT}" y1="{LOS}" x2="{FIELD_RIGHT}" y2="{LOS}" '
        f'stroke="{GOLD}" stroke-width="2.4" stroke-dasharray="2 5"/>'
    )
    parts.append(
        f'<text x="{FIELD_RIGHT-4}" y="{LOS-6}" text-anchor="end" '
        f'font-family="Space Grotesk, Arial, sans-serif" font-size="11" '
        f'fill="{GOLD}" opacity=".85" font-weight="700">LOS</text>'
    )
    return parts


def _oplayer(x, y, label, qb=False):
    fill = INK if qb else RED
    ring = GOLD if qb else "#fff"
    return (
        f'<g><circle cx="{x}" cy="{y}" r="13" fill="{fill}" stroke="{ring}" '
        f'stroke-width="2.2"/>'
        f'<text x="{x}" y="{y+4}" text-anchor="middle" '
        f'font-family="Space Grotesk, Arial, sans-serif" font-size="11.5" '
        f'font-weight="700" fill="#fff">{label}</text></g>'
    )


def _dplayer(x, y, label):
    return (
        f'<g><text x="{x}" y="{y+6}" text-anchor="middle" '
        f'font-family="Space Grotesk, Arial, sans-serif" font-size="20" '
        f'font-weight="800" fill="{NAVY}" stroke="#dfe7f2" stroke-width="1.4" '
        f'paint-order="stroke">✕</text>'
        f'<text x="{x}" y="{y-14}" text-anchor="middle" '
        f'font-family="Space Grotesk, Arial, sans-serif" font-size="9.5" '
        f'font-weight="700" fill="#cdd9ea">{label}</text></g>'
    )


def _route(points, color=None, label=None, dashed=False):
    color = color or "#ffffff"
    d = f'M {points[0][0]} {points[0][1]}'
    for px, py in points[1:]:
        d += f' L {px} {py}'
    dash = ' stroke-dasharray="6 5"' if dashed else ''
    out = [
        f'<path d="{d}" fill="none" stroke="{color}" stroke-width="3" '
        f'stroke-linecap="round" stroke-linejoin="round" marker-end="url(#arrow)"{dash}/>'
    ]
    if label:
        lx, ly = points[-1]
        out.append(
            f'<text x="{lx+8}" y="{ly-4}" font-family="Space Grotesk, Arial, sans-serif" '
            f'font-size="10.5" font-weight="700" fill="#fff">{label}</text>'
        )
    return out


def _block(x, y, tx, ty):
    """A blocking assignment: line from blocker toward defender, capped with a tee."""
    import math

    ang = math.atan2(ty - y, tx - x)
    cap = 7
    px, py = math.cos(ang + math.pi / 2) * cap, math.sin(ang + math.pi / 2) * cap
    ex, ey = x + math.cos(ang) * 24, y + math.sin(ang) * 24
    return [
        f'<line x1="{x}" y1="{y}" x2="{ex:.0f}" y2="{ey:.0f}" stroke="{GOLD}" '
        f'stroke-width="3" stroke-linecap="round"/>',
        f'<line x1="{ex-px:.0f}" y1="{ey-py:.0f}" x2="{ex+px:.0f}" y2="{ey+py:.0f}" '
        f'stroke="{GOLD}" stroke-width="3" stroke-linecap="round"/>',
    ]


def _handoff(x, y, tx, ty):
    return [
        f'<path d="M {x} {y} L {tx} {ty}" fill="none" stroke="{GOLD}" '
        f'stroke-width="2.4" stroke-dasharray="3 4" marker-end="url(#arrow-gold)"/>'
    ]


def _zone(x, y, label):
    return [
        f'<ellipse cx="{x}" cy="{y}" rx="34" ry="24" fill="rgba(11,35,64,.30)" '
        f'stroke="#9db6d6" stroke-width="1.6" stroke-dasharray="4 4"/>',
        f'<text x="{x}" y="{y+4}" text-anchor="middle" '
        f'font-family="Space Grotesk, Arial, sans-serif" font-size="10" '
        f'font-weight="700" fill="#dbe6f4">{label}</text>',
    ]


def _svg(title: str, body: list[str], caption: str) -> str:
    defs = (
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0 0 L10 5 L0 10 z" fill="#ffffff"/></marker>'
        '<marker id="arrow-gold" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0 0 L10 5 L0 10 z" fill="{GOLD}"/></marker>'
        '</defs>'
    )
    header = (
        f'<rect x="0" y="0" width="{W}" height="30" rx="0" fill="{INK}"/>'
        f'<text x="14" y="20" font-family="Space Grotesk, Arial, sans-serif" '
        f'font-size="13" font-weight="800" fill="{GOLD}" letter-spacing=".04em">'
        f'{_esc(title)}</text>'
    )
    footer = (
        f'<rect x="0" y="{H-26}" width="{W}" height="26" fill="{INK}"/>'
        f'<text x="14" y="{H-9}" font-family="Space Grotesk, Arial, sans-serif" '
        f'font-size="10.5" font-weight="600" fill="#f7f1e5">{_esc(caption)}</text>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        f'role="img" aria-label="{_esc(title)} — {_esc(caption)}" '
        f'width="{W}" height="{H}">{defs}'
        + "".join(_field())
        + "".join(body)
        + header + footer
        + "</svg>"
    )


def _esc(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ---------------------------------------------------------------------------
# Concept builders
# ---------------------------------------------------------------------------
def _build_play_action_boot(labels, caption):
    # Under-center play action bootleg to the right — Bieniemy staple.
    cx = 330
    body = []
    # Offensive line.
    for i, ox in enumerate(range(cx - 90, cx + 91, 45)):
        body.append(_oplayer(ox, LOS - 16, "OL"))
    qb_x, qb_y = cx, LOS + 20
    body.append(_oplayer(qb_x, qb_y, "QB", qb=True))
    rb_x, rb_y = cx - 26, LOS + 6
    body.append(_oplayer(rb_x, rb_y, "RB"))
    te_x = cx + 112
    body.append(_oplayer(te_x, LOS - 16, "TE"))
    wr1_x = FIELD_LEFT + 22
    wr2_x = FIELD_RIGHT - 24
    body.append(_oplayer(wr1_x, LOS - 16, "X"))
    body.append(_oplayer(wr2_x, LOS - 16, "Z"))
    # Fake handoff + QB bootleg path to the right.
    body += _handoff(qb_x, qb_y, rb_x - 30, rb_y - 26)
    body += _route(
        [(qb_x, qb_y), (cx + 70, qb_y + 4), (cx + 150, LOS + 2)],
        color=GOLD, label="boot", dashed=False,
    )
    # Routes: TE drag/flat, Z deep comeback, X backside post.
    body += _route([(te_x, LOS - 16), (cx + 176, LOS - 30)], color="#fff", label=labels.get("te", "flat"))
    body += _route([(wr2_x, LOS - 16), (wr2_x, LOS - 120), (wr2_x - 18, LOS - 96)],
                   color="#fff", label=labels.get("z", "comeback"))
    body += _route([(wr1_x, LOS - 16), (wr1_x + 60, LOS - 150)], color="#fff",
                   label=labels.get("x", "post"))
    # A single second-level defender flowing to the fake.
    body.append(_dplayer(cx - 40, LOS - 60, "LB"))
    body.append(_dplayer(cx + 150, LOS - 70, "CB"))
    return _svg(caption["title"], body, caption["blurb"])


def _build_four_verts(labels, caption):
    cx = 330
    body = []
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 16, "OL"))
    body.append(_oplayer(cx, LOS + 26, "QB", qb=True))
    body.append(_oplayer(cx - 30, LOS + 12, "RB"))
    receivers = [
        (FIELD_LEFT + 20, "X"),
        (FIELD_LEFT + 96, "SL"),
        (FIELD_RIGHT - 96, "SL2"),
        (FIELD_RIGHT - 22, "Z"),
    ]
    for rx, lbl in receivers:
        body.append(_oplayer(rx, LOS - 16, lbl.replace("SL2", "H").replace("SL", "Y")))
        # Vertical stems with the two seam runners bending to the hash.
        target_x = rx
        if lbl == "SL":
            target_x = cx - 70
        elif lbl == "SL2":
            target_x = cx + 70
        body += _route([(rx, LOS - 16), (rx, LOS - 60), (target_x, LOS - 150)],
                       color="#fff")
    # Cover 3 shell.
    body += _zone(cx - 120, LOS - 120, "C3")
    body += _zone(cx, LOS - 150, "FS")
    body += _zone(cx + 120, LOS - 120, "C3")
    for dx, lbl in ((cx - 60, "LB"), (cx + 60, "LB"), (cx, LOS - 78)):
        if isinstance(lbl, str):
            body.append(_dplayer(dx if isinstance(dx, (int, float)) else cx,
                                 LOS - 70, "LB"))
    return _svg(caption["title"], body, caption["blurb"])


def _build_mesh(labels, caption):
    cx = 330
    body = []
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 16, "OL"))
    body.append(_oplayer(cx, LOS + 26, "QB", qb=True))
    body.append(_oplayer(cx - 30, LOS + 12, "RB"))
    # Two crossers form the mesh point.
    lx, rx = FIELD_LEFT + 24, FIELD_RIGHT - 26
    body.append(_oplayer(lx, LOS - 16, "Y"))
    body.append(_oplayer(rx, LOS - 16, "H"))
    body += _route([(lx, LOS - 16), (cx - 10, LOS - 52), (rx - 10, LOS - 60)],
                   color="#fff", label="mesh")
    body += _route([(rx, LOS - 16), (cx + 10, LOS - 52), (lx + 10, LOS - 66)],
                   color="#fff")
    # Outside receivers: one corner, one sit.
    body.append(_oplayer(FIELD_LEFT + 96, LOS - 16, "X"))
    body.append(_oplayer(FIELD_RIGHT - 96, LOS - 16, "Z"))
    body += _route([(FIELD_LEFT + 96, LOS - 16), (FIELD_LEFT + 96, LOS - 70),
                    (FIELD_LEFT + 40, LOS - 118)], color="#fff", label=labels.get("x", "corner"))
    body += _route([(FIELD_RIGHT - 96, LOS - 16), (FIELD_RIGHT - 96, LOS - 66)],
                   color="#fff", label=labels.get("z", "sit"))
    # RB checkdown.
    body += _route([(cx - 30, LOS + 12), (cx + 60, LOS - 6)], color="#fff", label="check")
    # Man defenders trailing.
    for dx, dy, lbl in ((lx + 6, LOS - 40, "N"), (rx - 6, LOS - 40, "CB"),
                        (FIELD_LEFT + 92, LOS - 46, "CB"), (FIELD_RIGHT - 100, LOS - 46, "CB")):
        body.append(_dplayer(dx, dy, lbl))
    body += _zone(cx, LOS - 150, "FS")
    return _svg(caption["title"], body, caption["blurb"])


def _build_spacing_screen(labels, caption):
    cx = 330
    body = []
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 16, "OL"))
    body.append(_oplayer(cx, LOS + 26, "QB", qb=True))
    rb_x, rb_y = cx + 34, LOS + 10
    body.append(_oplayer(rb_x, rb_y, "RB"))
    # Screen: RB slips to the flat, two linemen release to lead.
    body += _route([(rb_x, rb_y), (cx + 120, LOS + 8), (cx + 150, LOS - 30)],
                   color=GOLD, label="screen")
    body += _block(cx + 45, LOS - 16, cx + 150, LOS - 60)
    body += _block(cx + 90, LOS - 16, cx + 180, LOS - 50)
    # Perimeter spacing hitches to clear coverage.
    body.append(_oplayer(FIELD_LEFT + 24, LOS - 16, "X"))
    body.append(_oplayer(FIELD_RIGHT - 24, LOS - 16, "Z"))
    body += _route([(FIELD_LEFT + 24, LOS - 16), (FIELD_LEFT + 24, LOS - 54)], color="#fff")
    body += _route([(FIELD_RIGHT - 24, LOS - 16), (FIELD_RIGHT - 24, LOS - 54)], color="#fff")
    for dx, dy, lbl in ((cx + 150, LOS - 74, "LB"), (FIELD_RIGHT - 60, LOS - 60, "CB")):
        body.append(_dplayer(dx, dy, lbl))
    return _svg(caption["title"], body, caption["blurb"])


def _build_inside_zone(labels, caption):
    cx = 330
    body = []
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 16, "OL"))
    body.append(_oplayer(cx, LOS + 18, "QB", qb=True))
    body.append(_oplayer(cx + 116, LOS - 16, "TE"))
    rb_x, rb_y = cx + 4, LOS + 34
    body.append(_oplayer(rb_x, rb_y, "RB"))
    body += _handoff(cx, LOS + 18, rb_x, rb_y - 6)
    # Zone-blocking down blocks.
    for ox in range(cx - 90, cx + 91, 45):
        body += _block(ox, LOS - 16, ox + 20, LOS - 44)
    body += _block(cx + 116, LOS - 16, cx + 140, LOS - 44)
    # Runner's downhill aiming point with a cutback option.
    body += _route([(rb_x, rb_y), (cx + 40, LOS - 4), (cx + 60, LOS - 70)],
                   color=GOLD, label="A-gap")
    body.append(_oplayer(FIELD_LEFT + 24, LOS - 16, "X"))
    body.append(_oplayer(FIELD_RIGHT - 24, LOS - 16, "Z"))
    for dx, dy, lbl in ((cx + 30, LOS - 40, "DT"), (cx - 30, LOS - 40, "DT"),
                        (cx + 60, LOS - 82, "LB"), (cx - 40, LOS - 82, "LB")):
        body.append(_dplayer(dx, dy, lbl))
    return _svg(caption["title"], body, caption["blurb"])


def _build_cover_two(labels, caption):
    cx = 330
    body = []
    # Defense-forward diagram (Spagnuolo shell). Offense small at bottom.
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 12, "OL"))
    body.append(_oplayer(cx, LOS + 20, "QB", qb=True))
    body.append(_oplayer(FIELD_LEFT + 24, LOS - 12, "X"))
    body.append(_oplayer(FIELD_RIGHT - 24, LOS - 12, "Z"))
    # Two deep safeties, five underneath zones.
    body += _zone(cx - 110, LOS - 150, "1/2")
    body += _zone(cx + 110, LOS - 150, "1/2")
    for zx, lbl in ((FIELD_LEFT + 40, "flat"), (cx - 70, "hook"),
                    (cx + 70, "hook"), (FIELD_RIGHT - 40, "flat")):
        body += _zone(zx, LOS - 80, lbl)
    # Front four rushers.
    for dx in (cx - 66, cx - 22, cx + 22, cx + 66):
        body.append(_dplayer(dx, LOS - 40, "DL"))
    return _svg(caption["title"], body, caption["blurb"])


def _build_zone_blitz(labels, caption):
    cx = 330
    body = []
    for ox in range(cx - 90, cx + 91, 45):
        body.append(_oplayer(ox, LOS - 12, "OL"))
    body.append(_oplayer(cx, LOS + 20, "QB", qb=True))
    body.append(_oplayer(cx - 30, LOS + 6, "RB"))
    body.append(_oplayer(FIELD_LEFT + 24, LOS - 12, "X"))
    body.append(_oplayer(FIELD_RIGHT - 24, LOS - 12, "Z"))
    # Simulated pressure: two LBs blitz A/B gaps, a DL drops to a hook zone.
    body += _route([(cx - 58, LOS - 60), (cx - 20, LOS - 26)], color=RED, label="blitz")
    body += _route([(cx + 58, LOS - 60), (cx + 22, LOS - 26)], color=RED, label="blitz")
    body.append(_dplayer(cx - 58, LOS - 60, "LB"))
    body.append(_dplayer(cx + 58, LOS - 60, "LB"))
    for dx in (cx - 44, cx, cx + 44):
        body.append(_dplayer(dx, LOS - 40, "DL"))
    body += _route([(cx + 88, LOS - 40), (cx + 60, LOS - 92)], color="#9db6d6",
                   label="drop", dashed=True)
    body.append(_dplayer(cx + 88, LOS - 40, "DE"))
    body += _zone(cx, LOS - 120, "3-deep")
    return _svg(caption["title"], body, caption["blurb"])


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
CONCEPTS: dict[str, dict] = {
    "play_action_boot": {
        "title": "Under-Center Play-Action Boot",
        "blurb": "Sell the run, move the pocket, put the QB on the perimeter with easy reads.",
        "build": _build_play_action_boot,
        "side": "offense",
    },
    "four_verts": {
        "title": "4 Verticals vs. Cover 3",
        "blurb": "Stress the deep middle: seams bend to the hash, outsides run past the corners.",
        "build": _build_four_verts,
        "side": "offense",
    },
    "mesh": {
        "title": "Mesh vs. Man",
        "blurb": "Rub-heavy crossers plus a corner/sit combo; RB checkdown as the outlet.",
        "build": _build_mesh,
        "side": "offense",
    },
    "spacing_screen": {
        "title": "RB Screen / Easy Offense",
        "blurb": "Let the line lead in space, take pressure off the QB, punish an aggressive rush.",
        "build": _build_spacing_screen,
        "side": "offense",
    },
    "inside_zone": {
        "title": "Under-Center Inside Zone",
        "blurb": "Downhill identity run: zone steps, one cut, win early downs.",
        "build": _build_inside_zone,
        "side": "offense",
    },
    "cover_two": {
        "title": "Spagnuolo Cover-2 Shell",
        "blurb": "Two deep, five under: take away the deep shot and rally to tackle.",
        "build": _build_cover_two,
        "side": "defense",
    },
    "zone_blitz": {
        "title": "Simulated Pressure / Zone Blitz",
        "blurb": "Show heat, drop a lineman, rush the second level — Spagnuolo's signature.",
        "build": _build_zone_blitz,
        "side": "defense",
    },
}

DEFAULT_CONCEPT = "play_action_boot"


def concept_keys() -> list[str]:
    return list(CONCEPTS.keys())


def render_concept(key: str, labels: dict | None = None, title: str | None = None,
                   blurb: str | None = None) -> tuple[str, str]:
    """Render a concept to SVG. Returns (svg_text, resolved_key)."""
    key = key if key in CONCEPTS else DEFAULT_CONCEPT
    spec = CONCEPTS[key]
    caption = {
        "title": title or spec["title"],
        "blurb": blurb or spec["blurb"],
    }
    svg = spec["build"](labels or {}, caption)
    return svg, key


def write_diagram(out_dir, slug: str, key: str, labels=None, title=None, blurb=None) -> dict:
    """Render a concept and write ``<slug>.svg`` to ``out_dir``.

    Returns a dict describing the diagram for the narrative JSON.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    svg, resolved = render_concept(key, labels, title, blurb)
    path = out_dir / f"{slug}.svg"
    path.write_text(svg, encoding="utf-8")
    return {
        "concept": resolved,
        "file": f"images/narrative/{slug}.svg",
        "title": title or CONCEPTS[resolved]["title"],
        "side": CONCEPTS[resolved]["side"],
    }


if __name__ == "__main__":  # simple manual smoke test
    import config as _c  # type: ignore

    _c.DIAGRAM_DIR.mkdir(parents=True, exist_ok=True)
    for k in CONCEPTS:
        write_diagram(_c.DIAGRAM_DIR, f"xo-{k}", k)
        print("wrote", k)
