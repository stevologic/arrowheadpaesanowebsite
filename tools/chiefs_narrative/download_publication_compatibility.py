"""Download this run's publication-compatibility artifact with bounded retry.

GitHub's artifact catalog can lag the upload-artifact finalize by about a
second, so a same-job download-artifact step may 404 even though the zip
exists. This module talks to the Actions REST API, retries only not-found,
and enforces the artifact digest before extract.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode, urljoin, urlparse

import requests

GITHUB_API = "https://api.github.com"
ARTIFACT_NAME = "publication-compatibility"
NOT_FOUND_MESSAGE = "Artifact not found for name: publication-compatibility"
RETRY_LIMIT = 5
RETRY_DELAY_SECONDS = 3
NOT_FOUND_EXIT = 1
HTTP_TIMEOUT_SECONDS = 20

KIND_OK = "ok"
KIND_NOT_FOUND = "not_found"
KIND_AUTH = "auth"
KIND_DIGEST_MISMATCH = "digest_mismatch"
KIND_BAD_ZIP = "bad_zip"
KIND_SERVER = "server"

_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


class RequestsTransport:
    """GET that follows redirects without forwarding Authorization cross-host."""

    def get(self, url: str, headers: dict[str, str] | None = None) -> requests.Response:
        headers = dict(headers or {})
        current = url
        response = requests.get(
            current,
            headers=headers,
            timeout=HTTP_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        hops = 0
        while response.is_redirect and hops < 5:
            location = response.headers.get("Location")
            if not location:
                break
            nxt = urljoin(current, location)
            hop_headers = dict(headers)
            if urlparse(nxt).netloc != urlparse(current).netloc:
                hop_headers.pop("Authorization", None)
            current = nxt
            hops += 1
            response = requests.get(
                current,
                headers=hop_headers,
                timeout=HTTP_TIMEOUT_SECONDS,
                allow_redirects=False,
            )
        return response


def list_artifacts_url(owner: str, repo: str, run_id: str) -> str:
    query = urlencode({"name": ARTIFACT_NAME})
    return (
        f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts?{query}"
    )  # same-run-exact-name


def zip_url(owner: str, repo: str, artifact_id: int) -> str:
    return f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip"


def is_retryable(kind: str) -> bool:
    return kind == KIND_NOT_FOUND  # retry-only-not-found


def emit_not_found() -> None:
    print(NOT_FOUND_MESSAGE, flush=True)  # frozen-not-found-message


def sha256_digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def auth_headers(token: str) -> dict[str, str]:
    return {**_API_HEADERS, "Authorization": f"Bearer {token}"}


def _status_kind(status: int) -> str | None:
    if status in (401, 403):
        return KIND_AUTH
    if status == 404:
        return KIND_NOT_FOUND
    if status in (200, 302):
        return None
    return KIND_SERVER


def _select_artifact(payload: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    wanted: list[dict[str, Any]] = []
    for artifact in payload.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if artifact.get("name") != ARTIFACT_NAME:
            continue
        workflow_run = artifact.get("workflow_run") or {}
        run_ident = workflow_run.get("id") if isinstance(workflow_run, dict) else None
        if run_ident is not None and int(run_ident) != int(run_id):
            continue
        wanted.append(artifact)
    return wanted[0] if wanted else None


def _extract_zip(data: bytes, dest: Path) -> str:
    dest.mkdir(parents=True, exist_ok=True)
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return KIND_BAD_ZIP
    try:
        if archive.testzip() is not None:
            return KIND_BAD_ZIP
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            parts = Path(name).parts
            if name.startswith("/") or ".." in parts:
                return KIND_BAD_ZIP
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    finally:
        archive.close()
    return KIND_OK


def one_attempt(
    *,
    owner: str,
    repo: str,
    run_id: str,
    dest: Path,
    token: str,
    transport: Any,
) -> tuple[str, str]:
    headers = auth_headers(token)
    listed = transport.get(list_artifacts_url(owner, repo, run_id), headers=headers)
    list_kind = _status_kind(listed.status_code)
    if list_kind is not None:
        return list_kind, f"list HTTP {listed.status_code}"
    try:
        payload = listed.json() if hasattr(listed, "json") else json.loads(listed.content)
    except (ValueError, json.JSONDecodeError, TypeError):
        return KIND_SERVER, "list JSON decode failed"
    if not isinstance(payload, dict):
        return KIND_SERVER, "list JSON was not an object"
    artifact = _select_artifact(payload, run_id)
    if artifact is None:
        return KIND_NOT_FOUND, NOT_FOUND_MESSAGE
    try:
        artifact_id = int(artifact["id"])
    except (KeyError, TypeError, ValueError):
        return KIND_SERVER, "artifact id missing"
    expected = artifact.get("digest")
    zipped = transport.get(zip_url(owner, repo, artifact_id), headers=headers)
    zip_kind = _status_kind(zipped.status_code)
    if zip_kind is not None:
        return zip_kind, f"zip HTTP {zipped.status_code}"
    blob = zipped.content
    if expected is None or str(expected).strip() == "":
        return KIND_DIGEST_MISMATCH, "digest mismatch: missing digest field"
    actual = sha256_digest(blob)
    if actual.lower() != str(expected).strip().lower():
        return KIND_DIGEST_MISMATCH, f"digest mismatch: {actual} != {expected}"
    extracted = _extract_zip(blob, dest)
    if extracted != KIND_OK:
        return KIND_BAD_ZIP, "bad zip"
    return KIND_OK, "ok"


def download_publication_compatibility(
    *,
    owner: str,
    repo: str,
    run_id: str,
    dest: str | Path,
    token: str,
    transport: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    if not run_id or not str(run_id).isdigit():
        print("run_id must be the numeric github.run_id", flush=True)
        return 1
    if not token:
        print("GITHUB_TOKEN is required", flush=True)
        return 1
    if "*" in ARTIFACT_NAME or not ARTIFACT_NAME:
        print("artifact name is not pinned", flush=True)
        return 1
    client = transport if transport is not None else RequestsTransport()
    target = Path(dest)
    last_kind = KIND_NOT_FOUND
    last_detail = NOT_FOUND_MESSAGE
    for attempt in range(1, RETRY_LIMIT + 1):
        print(f"try {attempt}/{RETRY_LIMIT}", flush=True)
        last_kind, last_detail = one_attempt(
            owner=owner,
            repo=repo,
            run_id=str(run_id),
            dest=target,
            token=token,
            transport=client,
        )
        if last_kind == KIND_OK:
            return 0
        if is_retryable(last_kind) and attempt < RETRY_LIMIT:
            sleep(RETRY_DELAY_SECONDS)
            continue
        break
    if last_kind == KIND_NOT_FOUND:
        emit_not_found()
        return NOT_FOUND_EXIT
    print(last_detail, flush=True)
    return 1


def _owner_repo(explicit_owner: str, explicit_repo: str) -> tuple[str, str]:
    if explicit_owner and explicit_repo:
        return explicit_owner, explicit_repo
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if "/" not in repository:
        raise SystemExit("GITHUB_REPOSITORY must be owner/repo")
    owner, repo = repository.split("/", 1)
    return owner, repo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--dest", required=True)
    parser.add_argument("--owner", default="")
    parser.add_argument("--repo", default="")
    args = parser.parse_args(argv)
    owner, repo = _owner_repo(args.owner, args.repo)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    return download_publication_compatibility(
        owner=owner,
        repo=repo,
        run_id=args.run_id,
        dest=args.dest,
        token=token,
    )


if __name__ == "__main__":
    raise SystemExit(main())
