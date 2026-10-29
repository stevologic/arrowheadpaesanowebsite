#!/usr/bin/env python3
"""Render fallback / wait / working hero states. Not published data."""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
OUT = ROOT / "docs" / "hero-evidence" / "renders"
WEEK8 = "2026-10-28-1750"
PIN_ID = "2115471247190438112"
WORKING_ID = "2115544469839745052"
X_HOSTS = (
    "cdn.syndication.twimg.com",
    "syndication.twitter.com",
    "platform.twitter.com",
    "platform.x.com",
    "cdn.api.twitter.com",
    "pbs.twimg.com",
    "video.twimg.com",
    "abs.twimg.com",
    "ton.twimg.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "api.x.com",
    "api.twitter.com",
)
WIDGETS_MOCK = """
(function () {
  var height = window.matchMedia("(max-width: 600px)").matches ? 540 : 609;
  var card = "<body style='margin:0;background:#15202b;color:#e7e9ea;font:15px/1.45 -apple-system,sans-serif'>"
    + "<div style='box-sizing:border-box;height:" + height + "px;padding:16px'>"
    + "<div style='display:flex;gap:12px'><div style='width:48px;height:48px;border-radius:50%;background:#c8102e'></div>"
    + "<div><strong>Kansas City Chiefs</strong> <span style='color:#8b98a5'>@Chiefs</span>"
    + "<p style='margin:8px 0 0'>Working pin fixture. Local embed stand-in so layout can be checked offline.</p></div></div>"
    + "<div style='margin-top:14px;height:220px;border-radius:16px;background:linear-gradient(180deg,#1d9bf0,#0f1419 70%)'></div>"
    + "<p style='color:#8b98a5;margin:14px 0 0'>10:41 AM · Oct 28, 2026 · X</p></div></body>";
  function paint() {
    document.querySelectorAll(".nrt-hero-media__embed").forEach(function (box) {
      if (box.querySelector("iframe")) return;
      var iframe = document.createElement("iframe");
      iframe.setAttribute("title", "working-pin-fixture");
      iframe.style.cssText = "display:block;width:100%;border:0;background:#15202b";
      iframe.style.height = height + "px";
      iframe.srcdoc = card;
      var quote = box.querySelector("blockquote");
      if (quote) quote.setAttribute("hidden", "");
      box.appendChild(iframe);
    });
  }
  window.twttr = {
    ready: function (cb) { setTimeout(function () { cb(window.twttr); }, 20); return window.twttr; },
    events: { bind: function (name, cb) { if (name === "rendered") setTimeout(cb, 40); } }
  };
  paint();
  setTimeout(paint, 50);
})();
"""
CLS_HEAD = """
<script>
window.__nrtCls = 0;
try {
  new PerformanceObserver(function (list) {
    list.getEntries().forEach(function (entry) {
      if (!entry.hadRecentInput) window.__nrtCls += entry.value;
    });
  }).observe({type: "layout-shift", buffered: true});
} catch (err) {}
</script>
"""
MEASURE_SNIPPET = """
<div id="hero-measure" style="position:fixed;bottom:0;left:0;right:0;background:#111;color:#ffcf53;font:12px/1.4 ui-monospace,monospace;padding:6px 10px;z-index:99"></div>
<script>
(function () {
  function report() {
    var fig = document.querySelector("figure.nrt-hero-media");
    var out = document.getElementById("hero-measure");
    if (!fig || !out) return;
    var visual = fig.querySelector(".nrt-hero-media__visual");
    var svg = fig.querySelector(".nrt-event-illustration");
    var iframe = fig.querySelector("iframe");
    var card = fig.getBoundingClientRect();
    var vb = visual ? visual.getBoundingClientRect() : { height: 0, width: 0 };
    var sb = svg ? svg.getBoundingClientRect() : { height: 0 };
    var ib = iframe ? iframe.getBoundingClientRect() : { height: 0 };
    var fill = Math.max(sb.height || 0, ib.height || 0);
    var payload = {
      state: fig.getAttribute("data-hero-state"),
      jsClass: document.documentElement.classList.contains("nrt-hero-js"),
      cardH: Math.round(card.height),
      visualW: Math.round(vb.width),
      visualH: Math.round(vb.height),
      svgH: Math.round(sb.height),
      iframeH: Math.round(ib.height),
      blankPx: Math.max(0, Math.round(vb.height - fill)),
      cls: Math.round((window.__nrtCls || 0) * 10000) / 10000
    };
    fig.setAttribute("data-measure", JSON.stringify(payload));
    document.documentElement.setAttribute("data-hero-measure", JSON.stringify(payload));
    out.textContent = JSON.stringify(payload);
    try {
      fetch("/__cls?" + encodeURIComponent(JSON.stringify(payload)));
    } catch (err) {}
  }
  report();
  setInterval(report, 200);
  window.addEventListener("load", report);
})();
</script>
"""


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _pin(post_id: str) -> dict:
    return {
        "id": post_id,
        "handle": "Chiefs",
        "url": f"https://x.com/Chiefs/status/{post_id}",
        "text": "Hero render fixture. Not published.",
        "alt": "Fixture pin for hero layout renders.",
        "postedAt": "2026-10-28T20:41:21Z",
        "sponsored": False,
        "mediaReviewed": True,
    }


def _build_fixture(post_id: str, *, mock_widgets: bool) -> tuple[tempfile.TemporaryDirectory, Path]:
    temp = tempfile.TemporaryDirectory()
    source = Path(temp.name) / "source"
    source.mkdir()
    (source / "data").mkdir()
    shutil.copytree(ROOT / "layouts" / "partials", source / "layouts" / "partials")
    for name in ("hero_scenes.json", "schedule_2026.json", "hero_media.json"):
        shutil.copyfile(ROOT / "data" / name, source / "data" / name)
    editions = "narrative" + "_editions"
    edition = json.loads((ROOT / "data" / editions / f"{WEEK8}.json").read_text(encoding="utf-8"))
    edition["slug"] = WEEK8
    (source / "data" / "fixtures.json").write_text(json.dumps([edition]), encoding="utf-8")
    (source / "data" / "hero_posts.json").write_text(json.dumps({
        "schemaVersion": 1,
        "allowlist": ["Chiefs", "ChiefsPR", "NFL", "TheAthletic", "TheAthleticNFL", "ESPN", "ESPNNFL"],
        "catalog": [],
        "pins": {WEEK8: _pin(post_id)},
    }), encoding="utf-8")
    public = source / "public"
    shutil.copytree(ROOT / "public" / "images" / "hero-scenes", public / "images" / "hero-scenes")
    css = public / "css"
    css.mkdir(parents=True)
    for name in ("editorial-layout.css", "style.css", "narrative.css", "v2.css"):
        src = ROOT / "public" / "css" / name
        if src.is_file():
            shutil.copyfile(src, css / name)
    (source / "hugo.yaml").write_text(
        "baseURL: http://127.0.0.1/\nstaticDir:\n  - public\n", encoding="utf-8"
    )
    (source / "layouts" / "index.html").write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<link rel="stylesheet" href="{{ "css/style.css" | relURL }}">'
        '<link rel="stylesheet" href="{{ "css/narrative.css" | relURL }}">'
        '<link rel="stylesheet" href="{{ "css/editorial-layout.css" | relURL }}">'
        '<style>body{margin:0;background:#0d0c0d;color:#eee5d4;font-family:Georgia,serif}'
        '.wrap{max-width:720px;margin:0 auto;padding:16px}</style>'
        + CLS_HEAD + '</head>'
        '<body><main class="wrap">{{ range .Site.Data.fixtures }}'
        '<section data-hero-slug="{{ .slug }}">'
        '{{ partial "nrt-hero-media.html" (dict "n" . "site" $.Site) }}'
        "</section>{{ end }}"
        "<section data-below><p>Copy under the hero so a late collapse would shift the page.</p>"
        "<p>Second paragraph of below-the-fold text for CLS.</p></section>"
        "</main>" + MEASURE_SNIPPET + "</body></html>\n",
        encoding="utf-8",
    )
    dist = Path(temp.name) / "site"
    result = subprocess.run(
        [HUGO, "--minify", "--destination", str(dist)],
        cwd=source, capture_output=True, text=True,
    )
    if result.returncode:
        temp.cleanup()
        raise SystemExit(result.stdout + result.stderr)
    html = (dist / "index.html").read_text(encoding="utf-8")
    if mock_widgets:
        html = html.replace(
            "https://platform.twitter.com/widgets.js",
            "/widgets-mock.js",
        )
        (dist / "index.html").write_text(html, encoding="utf-8")
        (dist / "widgets-mock.js").write_text(WIDGETS_MOCK, encoding="utf-8")
    stripped = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.I | re.S)
    stripped = re.sub(r'<div id="hero-measure"[^>]*>.*?</div>', "", stripped, flags=re.I | re.S)
    (dist / "index-js-off.html").write_text(stripped, encoding="utf-8")
    committed = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8"))
    if committed.get("pins"):
        temp.cleanup()
        raise SystemExit("published sidecar is not empty; aborting fixture render")
    return temp, dist


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, int, dict]:
    port = _free_port()
    store: dict = {"latest": None}

    class Static(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/__cls":
                try:
                    store["latest"] = json.loads(unquote(parsed.query))
                except (ValueError, TypeError):
                    store["latest"] = {"error": "bad-cls", "raw": parsed.query}
                self.send_response(204)
                self.end_headers()
                return
            path = directory / parsed.path.lstrip("/")
            if path.is_dir():
                path = path / "index.html"
            if not path.is_file():
                self.send_error(404)
                return
            data = path.read_bytes()
            ctypes = {
                ".html": "text/html",
                ".css": "text/css",
                ".js": "text/javascript",
                ".svg": "image/svg+xml",
                ".png": "image/png",
            }
            ctype = ctypes.get(path.suffix, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", port), Static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port, store


def _chrome(url: str, png: Path, w: int, h: int, hosts: tuple[str, ...], *,
            budget_ms: int) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    if png.exists():
        png.unlink()
    user = tempfile.mkdtemp(prefix="hero-chrome-")
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", f"--user-data-dir={user}",
        f"--window-size={w},{h}",
        f"--virtual-time-budget={budget_ms}",
        f"--screenshot={png}",
    ]
    if hosts:
        cmd.append("--host-resolver-rules=" + ",".join(f"MAP {host} 127.0.0.1" for host in hosts))
    cmd.append(url)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(max(20, budget_ms // 200 + 8)):
            if png.is_file() and png.stat().st_size > 8000:
                time.sleep(0.4)
                break
            if proc.poll() is not None:
                break
            time.sleep(0.4)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    shutil.rmtree(user, ignore_errors=True)


def _assert_published_clean() -> None:
    pins = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8")).get("pins")
    if pins:
        raise SystemExit("published sidecar was mutated")


def main() -> int:
    if not Path(HUGO).is_file() or not CHROME:
        print("hugo or chrome missing", file=sys.stderr)
        return 1
    cdn = ("cdn.syndication.twimg.com", "syndication.twitter.com")
    all404 = cdn + ("platform.twitter.com", "platform.x.com")
    cases = (
        ("wait-cdnonly", PIN_ID, cdn, False, True, 2500),
        ("wait-all404", PIN_ID, all404, False, True, 2500),
        ("wait-blocked", PIN_ID, X_HOSTS, False, True, 2500),
        ("fallback-cdnonly", PIN_ID, cdn, False, True, 9500),
        ("fallback-all404", PIN_ID, all404, False, True, 9500),
        ("fallback-blocked", PIN_ID, X_HOSTS, False, True, 9500),
        ("working", WORKING_ID, X_HOSTS, True, True, 2500),
        ("wait-blocked", PIN_ID, X_HOSTS, False, False, 1500),
        ("fallback-cdnonly", PIN_ID, cdn, False, False, 1500),
        ("working", WORKING_ID, X_HOSTS, True, False, 1500),
    )
    sizes = (("390x844", 390, 844), ("1280x800", 1280, 800))
    metrics = {}
    built = {}
    servers = []
    try:
        for key in (("fail", PIN_ID, False), ("work", WORKING_ID, True)):
            label, post_id, mock = key
            temp, dist = _build_fixture(post_id, mock_widgets=mock)
            static, port, store = _serve(dist)
            built[label] = (temp, dist, static, port, store)
            servers.append(static)
        for name, post_id, hosts, mock, js, budget in cases:
            site = built["work" if mock else "fail"]
            store = site[4]
            url = f"http://127.0.0.1:{site[3]}/{'index-js-off.html' if not js else ''}"
            js_label = "js-on" if js else "js-off"
            for label, w, h in sizes:
                store["latest"] = None
                png = OUT / f"{name}-{label}-{js_label}.png"
                _chrome(url, png, w, h, hosts, budget_ms=budget)
                if not png.is_file() or png.stat().st_size < 8000:
                    raise SystemExit(f"{png.name}: screenshot missing or empty")
                measured = store["latest"] or {}
                metrics[png.name] = {
                    "bytes": png.stat().st_size,
                    "js": js,
                    "url": url,
                    **measured,
                }
                print(f"ok {png.name} bytes={png.stat().st_size} measure={measured}")
                aliases = {
                    "fallback-cdnonly": f"deleted-cdnonly-{label}-{js_label}.png",
                    "fallback-blocked": f"deleted-blocked-{label}-{js_label}.png",
                    "wait-blocked": f"wait-{label}-{js_label}.png",
                    "fallback-cdnonly-legacy": f"fallback-404-{label}-{js_label}.png",
                }
                if name in aliases:
                    shutil.copyfile(png, OUT / aliases[name])
                if name == "fallback-cdnonly":
                    shutil.copyfile(png, OUT / f"fallback-404-{label}-{js_label}.png")
        (OUT / "render-metrics.json").write_text(
            json.dumps(metrics, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote hero state renders under {OUT}")
        return 0
    finally:
        for server in servers:
            server.shutdown()
        for temp, _dist, _static, _port, _store in built.values():
            temp.cleanup()
        _assert_published_clean()


if __name__ == "__main__":
    raise SystemExit(main())
