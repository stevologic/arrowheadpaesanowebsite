"""Narrative comments: validation, spam gates, and moderation.

The Hugo site is static GitHub Pages, so this package is the $0 backend:

* ``service.py`` — store, honeypot, rate limit, hide/delete (tested offline).
* ``server.py`` — local stdlib HTTP API for previews and screenshots.
* ``worker.js`` — Cloudflare Worker + KV port for production ($0 free tier).

Fans do not need a GitHub (or any) account. Stephen moderates with
``COMMENTS_ADMIN_TOKEN`` (env/secret — never commit the real value).
"""

from .service import CommentError, CommentStore

__all__ = ["CommentError", "CommentStore"]
