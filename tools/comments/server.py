"""Local comments HTTP API (stdlib only) for previews and screenshots.

  COMMENTS_ADMIN_TOKEN=dev-admin \\
  COMMENTS_TURNSTILE_MODE=offline \\
  python tools/comments/server.py

Listens on 127.0.0.1:8787. Production uses tools/comments/worker.js.
"""
from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.comments.service import CommentError, CommentStore  # noqa: E402

HOST = os.environ.get("COMMENTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("COMMENTS_PORT", "8787"))
PROD_ORIGINS = {
    origin.strip()
    for origin in os.environ.get(
        "COMMENTS_CORS_ORIGINS",
        "https://arrowheadpaesano.com,https://www.arrowheadpaesano.com",
    ).split(",")
    if origin.strip()
}
DEV_MODE = os.environ.get("COMMENTS_DEV", "").strip().lower() in {"1", "true", "yes"}


def _store() -> CommentStore:
    token = os.environ.get("COMMENTS_ADMIN_TOKEN", "").strip()
    if not token:
        # Local-only default so screenshots work. The Worker refuses an empty secret.
        token = "dev-admin"
    return CommentStore(
        admin_token=token,
        turnstile_mode=os.environ.get("COMMENTS_TURNSTILE_MODE", "offline"),
    )


STORE = _store()


def _json_bytes(payload: Any, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload).encode("utf-8")


def handle(method: str, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, bytes]:
    try:
        return _handle(method, path, body, headers)
    except CommentError as exc:
        return _json_bytes({"error": exc.message}, exc.status)


def _handle(method: str, path: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, bytes]:
    parsed = urlparse(path)
    parts = [part for part in parsed.path.split("/") if part]
    query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
    ip = headers.get("x-forwarded-for", headers.get("x-real-ip", "127.0.0.1")).split(",")[0].strip()

    if method == "GET" and parts == ["comments"]:
        slug = query.get("slug", "")
        if query.get("all") == "1" or query.get("hidden") == "1":
            STORE.require_admin(headers.get("authorization"), ip)
            return _json_bytes({"comments": STORE.list_admin(slug or None), "nextSlug": None})
        STORE.hit_get(ip)
        return _json_bytes(STORE.list_public(slug, limit=query.get("limit"), after=query.get("after", "")))

    if method == "POST" and parts == ["comments"]:
        row = STORE.post(
            body,
            ip=ip,
            turnstile_token=str(body.get("turnstileToken") or headers.get("x-turnstile-token") or ""),
        )
        if row is None:
            return _json_bytes({"ok": True, "ignored": True}, 201)
        return _json_bytes({"comment": row}, 201)

    if method == "POST" and len(parts) == 3 and parts[0] == "comments" and parts[2] == "hide":
        STORE.require_admin(headers.get("authorization"), ip)
        return _json_bytes({"comment": STORE.hide(parts[1], True)})

    if method == "POST" and len(parts) == 3 and parts[0] == "comments" and parts[2] == "unhide":
        STORE.require_admin(headers.get("authorization"), ip)
        return _json_bytes({"comment": STORE.hide(parts[1], False)})

    if method == "DELETE" and len(parts) == 2 and parts[0] == "comments":
        STORE.require_admin(headers.get("authorization"), ip)
        STORE.delete(parts[1])
        return _json_bytes({"ok": True})

    raise CommentError("Not found.", 404)


class Handler(BaseHTTPRequestHandler):
    def _cors_origin(self) -> str:
        origin = self.headers.get("Origin", "")
        if origin in PROD_ORIGINS:
            return origin
        if DEV_MODE and (
            origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:")
        ):
            return origin
        return ""

    def _send(self, status: int, payload: bytes, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        origin = self._cors_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Turnstile-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if "/hide" in self.path or "/unhide" in self.path or self.command == "DELETE" or "all=1" in self.path or "hidden=1" in self.path:
            self.send_header("X-Robots-Tag", "noindex, nofollow")
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body: dict[str, Any] = {}
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._send(400, json.dumps({"error": "Invalid JSON."}).encode("utf-8"))
                return
            if isinstance(parsed, dict):
                body = parsed
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, payload = handle(method, self.path, body, headers)
        self._send(status, payload)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"comments API on http://{HOST}:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
