#!/usr/bin/env python3
"""Render a fixture pin with X syndication 404 / all-X blocked. Not published data."""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
HUGO = shutil.which("hugo") or str(Path.home() / ".local" / "hugo" / "hugo")
CHROME = shutil.which("google-chrome") or shutil.which("chromium")
OUT = ROOT / "docs" / "hero-evidence" / "renders"
WEEK8 = "2026-10-28-1750"
PIN_ID = "2115471247190438112"
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


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _build_fixture() -> tuple[tempfile.TemporaryDirectory, Path]:
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
        "pins": {
            WEEK8: {
                "id": PIN_ID,
                "handle": "Chiefs",
                "url": f"https://x.com/Chiefs/status/{PIN_ID}",
                "text": "Deleted-post fixture. Not published.",
                "alt": "Fixture pin for a deleted official post.",
                "postedAt": "2026-10-28T20:41:21Z",
                "sponsored": False,
                "mediaReviewed": True,
            }
        },
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
        '.wrap{max-width:720px;margin:0 auto;padding:16px}</style></head>'
        '<body><main class="wrap">{{ range .Site.Data.fixtures }}'
        '<section data-hero-slug="{{ .slug }}">'
        '{{ partial "nrt-hero-media.html" (dict "n" . "site" $.Site) }}'
        "</section>{{ end }}</main></body></html>\n",
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
    committed = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8"))
    if committed.get("pins"):
        temp.cleanup()
        raise SystemExit("published sidecar is not empty; aborting fixture render")
    return temp, dist


def _serve(directory: Path) -> tuple[ThreadingHTTPServer, int]:
    port = _free_port()

    class Static(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            path = directory / urlsplit(self.path).path.lstrip("/")
            if path.is_dir():
                path = path / "index.html"
            if not path.is_file():
                self.send_error(404)
                return
            data = path.read_bytes()
            ctype = "text/html" if path.suffix == ".html" else "text/css" if path.suffix == ".css" else "image/svg+xml"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", port), Static)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def _chrome(url: str, png: Path, w: int, h: int, hosts: tuple[str, ...]) -> str:
    png.parent.mkdir(parents=True, exist_ok=True)
    if png.exists():
        png.unlink()
    user = tempfile.mkdtemp(prefix="hero-chrome-")
    rules = ",".join(f"MAP {host} 127.0.0.1" for host in hosts)
    cmd = [
        CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--hide-scrollbars", f"--user-data-dir={user}",
        f"--window-size={w},{h}",
        f"--host-resolver-rules={rules}",
        "--virtual-time-budget=9000",
        f"--screenshot={png}",
        url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(40):
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
    return ""


def main() -> int:
    if not Path(HUGO).is_file() or not CHROME:
        print("hugo or chrome missing", file=sys.stderr)
        return 1
    temp, dist = _build_fixture()
    static, site_port = _serve(dist)
    url = f"http://127.0.0.1:{site_port}/"
    cases = (
        ("deleted-cdnonly", ("cdn.syndication.twimg.com",)),
        ("deleted-blocked", X_HOSTS),
    )
    try:
        for name, hosts in cases:
            for label, w, h in (("390x844", 390, 844), ("1280x800", 1280, 800)):
                png = OUT / f"{name}-{label}-js-on.png"
                _chrome(url, png, w, h, hosts)
                if not png.is_file() or png.stat().st_size < 10000:
                    raise SystemExit(f"{png.name}: screenshot missing or empty")
                print(f"ok {png.name} bytes={png.stat().st_size}")
        print(f"wrote deleted renders under {OUT}")
        return 0
    finally:
        static.shutdown()
        temp.cleanup()
        pins = json.loads((ROOT / "data" / "hero_posts.json").read_text(encoding="utf-8")).get("pins")
        if pins:
            raise SystemExit("published sidecar was mutated")


if __name__ == "__main__":
    raise SystemExit(main())
