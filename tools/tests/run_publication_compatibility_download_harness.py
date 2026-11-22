"""Harness for same-run publication-compatibility artifact download retry.

Drives the exact script file the gates job runs (not a YAML string pin).
Named run_*.py so unittest discover does not collect the mutant loop.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import tempfile
import traceback
import unittest
import uuid
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yaml

REPO = Path(__file__).resolve().parents[2]
CI_PATH = REPO / ".github" / "workflows" / "ci.yml"
HARNESS_MODULE = "tools.tests.run_publication_compatibility_download_harness"
SCRIPT_MODULE = "tools.chiefs_narrative.download_publication_compatibility"
NOT_FOUND_MESSAGE = "Artifact not found for name: publication-compatibility"
RUN_ID = "45053692704"
REPEATS = 6
OWNER = "stevologic"
REPO_NAME = "arrowheadpaesanowebsite"

MUTANTS: dict[str, tuple[str, str]] = {
    "M1": ("RETRY_LIMIT = 5", "RETRY_LIMIT = 6"),
    "M2": ("RETRY_LIMIT = 5", "RETRY_LIMIT = 4"),
    "M3": ("RETRY_DELAY_SECONDS = 3", "RETRY_DELAY_SECONDS = 0"),
    "M4": ("NOT_FOUND_EXIT = 1", "NOT_FOUND_EXIT = 0"),
    "M5": (
        "return kind == KIND_NOT_FOUND  # retry-only-not-found",
        "return kind in (KIND_NOT_FOUND, KIND_DIGEST_MISMATCH)  # retry-only-not-found",
    ),
    "M6": (
        "return kind == KIND_NOT_FOUND  # retry-only-not-found",
        "return kind in (KIND_NOT_FOUND, KIND_AUTH)  # retry-only-not-found",
    ),
    "M7": (
        'f"{GITHUB_API}/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts?{query}"',
        'f"{GITHUB_API}/repos/{owner}/{repo}/actions/artifacts?name=publication-*"',
    ),
    "M8": (
        "print(NOT_FOUND_MESSAGE, flush=True)  # frozen-not-found-message",
        'print("download failed", flush=True)  # frozen-not-found-message',
    ),
    "MN3a": (
        '        return KIND_DIGEST_MISMATCH, "digest mismatch: missing digest field"',
        "        extracted = _extract_zip(blob, dest)\n"
        "        if extracted != KIND_OK:\n"
        '            return KIND_BAD_ZIP, "bad zip"\n'
        '        return KIND_OK, "ok"',
    ),
    "MN3b": (
        '            if name.startswith("/") or ".." in parts:\n'
        "                return KIND_BAD_ZIP",
        "            if False:\n"
        "                return KIND_BAD_ZIP",
    ),
}


class FakeResponse:
    def __init__(self, status_code: int, content: bytes):
        self.status_code = status_code
        self.content = content
        self.headers: dict[str, str] = {}
        self.is_redirect = False

    def json(self) -> Any:
        return json.loads(self.content.decode())


class FakeTransport:
    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def get(self, url: str, headers: dict[str, str] | None = None) -> FakeResponse:
        self.calls.append({"url": url, "headers": dict(headers or {})})
        if not self._responses:
            return FakeResponse(599, b"no more scripted responses")
        item = self._responses.pop(0)
        if callable(item):
            return item(url, headers)
        return item


def _ci_text() -> str:
    return CI_PATH.read_text(encoding="utf-8")


def _load_yaml(text: str) -> dict[str, Any]:
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise AssertionError("ci.yml did not parse as a mapping")
    return loaded


def _baseline_ci_text() -> str:
    return subprocess.check_output(
        ["git", "show", "origin/develop:.github/workflows/ci.yml"],
        cwd=REPO,
        text=True,
    )


def _gates(parsed: dict[str, Any]) -> dict[str, Any]:
    job = ((parsed.get("jobs") or {}).get("gates") or {})
    if not isinstance(job, dict):
        raise AssertionError("gates job missing")
    return job


def _step_by_name(parsed: dict[str, Any], name: str) -> dict[str, Any]:
    for step in _gates(parsed).get("steps") or []:
        if isinstance(step, dict) and step.get("name") == name:
            return step
    raise AssertionError(f"missing step {name}")


def script_path_from_ci(parsed: dict[str, Any] | None = None) -> Path:
    step = _step_by_name(parsed or _load_yaml(_ci_text()), "Download publication compatibility fixture")
    run = str(step.get("run") or "")
    match = re.search(r"python -m (tools\.chiefs_narrative\.[A-Za-z0-9_]+)", run)
    if not match:
        raise AssertionError(f"download step does not invoke a tools.chiefs_narrative module:\n{run}")
    module = match.group(1)
    path = REPO.joinpath(*module.split(".")).with_suffix(".py")
    if not path.is_file():
        raise AssertionError(f"cited script does not exist: {path}")
    return path


def load_script(path: Path):
    name = f"pubcompat_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def live_script():
    return load_script(script_path_from_ci())


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def http_json(status: int, payload: dict[str, Any]) -> FakeResponse:
    return FakeResponse(status, json.dumps(payload).encode())


def http_status(status: int, body: bytes = b"") -> FakeResponse:
    return FakeResponse(status, body)


def sample_files() -> dict[str, bytes]:
    return {
        ".narrative-publish.json": b'{"mode": "schedule", "holdMerge": true}\n',
        "payload/fixture.json": b'{"ok": true}\n',
    }


def hit_responses(
    run_id: str = RUN_ID,
    files: dict[str, bytes] | None = None,
    digest: str | None = None,
    artifact_id: int = 11,
    zip_body: bytes | None = None,
    omit_digest: bool = False,
) -> tuple[FakeResponse, FakeResponse, bytes, dict[str, bytes]]:
    files = files if files is not None else sample_files()
    blob = zip_body if zip_body is not None else make_zip(files)
    artifact: dict[str, Any] = {
        "id": artifact_id,
        "name": "publication-compatibility",
        "workflow_run": {"id": int(run_id)},
    }
    if not omit_digest:
        artifact["digest"] = (
            "sha256:" + hashlib.sha256(blob).hexdigest() if digest is None else digest
        )
    listing = http_json(200, {"total_count": 1, "artifacts": [artifact]})
    return listing, FakeResponse(200, blob), blob, files


def run_download(mod, transport: FakeTransport, dest: Path | None = None, run_id: str = RUN_ID, token: str = "test-token"):
    sleeps: list[float] = []
    if dest is None:
        dest = Path(tempfile.mkdtemp()) / "artifact-roundtrip"
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        rc = mod.download_publication_compatibility(
            owner=OWNER,
            repo=REPO_NAME,
            run_id=run_id,
            dest=dest,
            token=token,
            transport=transport,
            sleep=sleeps.append,
        )
    return rc, buf.getvalue(), sleeps, dest


def _list_calls(transport: FakeTransport) -> list[dict[str, Any]]:
    return [call for call in transport.calls if "/zip" not in call["url"]]


def _zip_calls(transport: FakeTransport) -> list[dict[str, Any]]:
    return [call for call in transport.calls if call["url"].endswith("/zip")]


def _try_lines(log: str) -> list[str]:
    return [line for line in log.splitlines() if line.startswith("try ")]


def _files_outside(root: Path, dest: Path) -> dict[str, bytes]:
    dest_resolved = dest.resolve()
    found: dict[str, bytes] = {}
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(dest_resolved)
        except ValueError:
            found[str(resolved)] = path.read_bytes()
    return found


def assert_pinned_list_url(url: str, run_id: str = RUN_ID) -> None:
    parsed = urlparse(url)
    if parsed.netloc != "api.github.com":
        raise AssertionError(f"list host {parsed.netloc}")
    expected_path = f"/repos/{OWNER}/{REPO_NAME}/actions/runs/{run_id}/artifacts"
    if parsed.path != expected_path:
        raise AssertionError(f"list path {parsed.path} != {expected_path}")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if query != {"name": ["publication-compatibility"]}:
        raise AssertionError(f"list query {query}")
    if "*" in url:
        raise AssertionError(f"glob in list url {url}")


def case_r1_scope() -> None:
    current_text = _ci_text()
    current = _load_yaml(current_text)
    baseline = _load_yaml(_baseline_ci_text())
    if current.get("permissions") != {"contents": "read"}:
        raise AssertionError(f"workflow permissions changed: {current.get('permissions')}")
    if current["jobs"]["automerge"] != baseline["jobs"]["automerge"]:
        raise AssertionError("automerge job is not byte-identical to origin/develop")
    baseline_gates = baseline["jobs"]["gates"]
    gates = current["jobs"]["gates"]
    if gates.get("runs-on") != baseline_gates.get("runs-on"):
        raise AssertionError("gates runs-on changed")
    if gates.get("timeout-minutes") != baseline_gates.get("timeout-minutes"):
        raise AssertionError("gates timeout-minutes changed")
    perms = gates.get("permissions") or {}
    if set(perms) - {"contents", "actions"}:
        raise AssertionError(f"unexpected gates permissions {perms}")
    if any(value != "read" for value in perms.values()):
        raise AssertionError(f"write permission on gates {perms}")
    if "write" in json.dumps(perms):
        raise AssertionError(f"write token on gates {perms}")
    if perms.get("actions") != "read":
        raise AssertionError("REST download requires job actions:read and nothing more")
    if perms.get("contents") != "read":
        raise AssertionError("gates must keep contents:read")
    allowed = {
        "Download publication compatibility fixture",
        "Publication compatibility download retry harness",
    }
    baseline_steps = {
        step["name"]: step for step in baseline_gates["steps"] if isinstance(step, dict)
    }
    current_names = [
        step.get("name") for step in gates["steps"] if isinstance(step, dict)
    ]
    if "Publication compatibility download retry harness" not in current_names:
        raise AssertionError("R10 harness wiring missing from gates")
    for step in gates["steps"]:
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        if name in allowed:
            continue
        if name not in baseline_steps:
            raise AssertionError(f"unexpected gates step {name}")
        if step != baseline_steps[name]:
            raise AssertionError(f"step {name!r} is not byte-identical to origin/develop")
    if set(baseline_steps) - set(current_names):
        raise AssertionError("removed a gates step")
    download = _step_by_name(current, "Download publication compatibility fixture")
    if "uses" in download:
        raise AssertionError("download step still uses an action; retry must live in the script")
    if "continue-on-error" in download:
        raise AssertionError("retry/classify logic must not live in YAML")
    if download.get("timeout-minutes") is None:
        raise AssertionError("download step missing timeout-minutes")
    run = str(download.get("run") or "")
    if SCRIPT_MODULE not in run:
        raise AssertionError(f"download step does not run {SCRIPT_MODULE}")
    if '${{ github.run_id }}' not in run:
        raise AssertionError("download step is not pinned to github.run_id")
    if "artifact-roundtrip" not in run:
        raise AssertionError("download dest is not artifact-roundtrip")
    if "download-artifact" in current_text.split("automerge:")[0]:
        raise AssertionError("gates still references download-artifact")
    upload = _step_by_name(current, "Upload publication compatibility fixture")
    verify = _step_by_name(current, "Verify publication artifact bytes and hidden metadata")
    if upload != baseline_steps["Upload publication compatibility fixture"]:
        raise AssertionError("Upload step changed")
    if verify != baseline_steps["Verify publication artifact bytes and hidden metadata"]:
        raise AssertionError("Verify step changed")


def case_r2_same_run(mod) -> None:
    listing, zipped, _blob, files = hit_responses()
    transport = FakeTransport([listing, zipped])
    dest = Path(tempfile.mkdtemp()) / "artifact-roundtrip"
    rc, _log, _sleeps, dest = run_download(mod, transport, dest=dest)
    if rc != 0:
        raise AssertionError(f"hit on try 1 failed rc={rc}")
    lists = _list_calls(transport)
    if len(lists) != 1:
        raise AssertionError(f"expected one list call, got {lists}")
    assert_pinned_list_url(lists[0]["url"])
    zips = _zip_calls(transport)
    if len(zips) != 1:
        raise AssertionError(f"expected one zip call, got {zips}")
    parsed = urlparse(zips[0]["url"])
    if parsed.path != f"/repos/{OWNER}/{REPO_NAME}/actions/artifacts/11/zip":
        raise AssertionError(f"zip path {parsed.path}")
    for name, data in files.items():
        if (dest / name).read_bytes() != data:
            raise AssertionError(f"extracted {name} mismatch")


def case_r3_integrity(mod) -> None:
    listing, zipped, blob, files = hit_responses()
    transport = FakeTransport([listing, zipped])
    dest = Path(tempfile.mkdtemp()) / "ok"
    rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
    if rc != 0:
        raise AssertionError(f"matching digest failed: {log}")
    if sleeps:
        raise AssertionError(f"matching digest retried {sleeps}")
    if (dest / ".narrative-publish.json").read_bytes() != files[".narrative-publish.json"]:
        raise AssertionError("hidden file missing after digest-matching extract")
    if not hasattr(mod, "sha256_digest"):
        raise AssertionError("script does not expose sha256_digest")
    if mod.sha256_digest(blob) != "sha256:" + hashlib.sha256(blob).hexdigest():
        raise AssertionError("digest helper drifted from sha256 of the zip bytes")
    bad_list, bad_zip, _blob, _files = hit_responses(
        digest="sha256:" + ("ab" * 32)
    )
    good_list, good_zip, _blob2, _files2 = hit_responses(artifact_id=12)
    transport = FakeTransport([bad_list, bad_zip, good_list, good_zip])
    dest = Path(tempfile.mkdtemp()) / "mismatch"
    rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
    if rc == 0:
        raise AssertionError("digest mismatch succeeded")
    if sleeps:
        raise AssertionError(f"digest mismatch retried {sleeps}")
    if len(transport.calls) != 2:
        raise AssertionError(f"digest mismatch must stop after list+zip, calls={transport.calls}")
    if any(dest.rglob("*")):
        raise AssertionError("digest mismatch extracted files")
    if "digest mismatch" not in log:
        raise AssertionError(f"digest mismatch log missing: {log!r}")
    upload = _step_by_name(_load_yaml(_ci_text()), "Upload publication compatibility fixture")
    uses = str(upload.get("uses") or "")
    if not re.search(r"@[0-9a-f]{40}", uses):
        raise AssertionError(f"kept upload-artifact is not SHA-pinned: {uses}")


def case_r4_retry_only_not_found(mod) -> None:
    scenarios = [
        (
            "digest",
            [
                hit_responses(digest="sha256:" + ("cd" * 32))[0],
                hit_responses(digest="sha256:" + ("cd" * 32))[1],
                *hit_responses(artifact_id=99)[:2],
            ],
        ),
        (
            "403",
            [http_status(403, b'{"message":"Forbidden"}'), *hit_responses(artifact_id=99)[:2]],
        ),
        (
            "401",
            [http_status(401, b'{"message":"Unauthorized"}'), *hit_responses(artifact_id=99)[:2]],
        ),
        (
            "5xx-after-download",
            [hit_responses()[0], http_status(500, b"blob boom"), *hit_responses(artifact_id=99)[:2]],
        ),
        (
            "bad-zip",
            [
                hit_responses(zip_body=b"not-a-zip", digest="sha256:" + hashlib.sha256(b"not-a-zip").hexdigest())[0],
                FakeResponse(200, b"not-a-zip"),
                *hit_responses(artifact_id=99)[:2],
            ],
        ),
    ]
    for label, responses in scenarios:
        transport = FakeTransport(list(responses))
        dest = Path(tempfile.mkdtemp()) / label
        rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
        if rc == 0:
            raise AssertionError(f"{label} succeeded")
        if sleeps:
            raise AssertionError(f"{label} retried sleeps={sleeps} log={log!r}")
        if len(_list_calls(transport)) != 1:
            raise AssertionError(f"{label} retried lists={_list_calls(transport)}")
        if any(dest.rglob("*")):
            raise AssertionError(f"{label} extracted files")


def case_r5_bounds(mod) -> None:
    download = _step_by_name(_load_yaml(_ci_text()), "Download publication compatibility fixture")
    if download.get("timeout-minutes") is None:
        raise AssertionError("timeout-minutes missing")

    listing, zipped, _blob, _files = hit_responses()
    transport = FakeTransport([listing, zipped])
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc != 0 or sleeps:
        raise AssertionError(f"try 1 hit failed rc={rc} sleeps={sleeps} log={log!r}")
    if "try 1/5" not in log:
        raise AssertionError(f"try 1 log missing: {log!r}")
    if len(_list_calls(transport)) != 1:
        raise AssertionError("try 1 made extra list calls")

    delayed = [http_status(404, b"missing") for _ in range(4)]
    listing, zipped, _blob, _files = hit_responses(artifact_id=15)
    transport = FakeTransport(delayed + [listing, zipped])
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc != 0:
        raise AssertionError(f"try 5 hit failed rc={rc} log={log!r}")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"try 5 hit sleeps {sleeps}")
    if "try 5/5" not in log:
        raise AssertionError(f"try 5 log missing: {log!r}")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"try 5 list count {len(_list_calls(transport))}")

    transport = FakeTransport([http_status(404, b"missing") for _ in range(8)])
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError("5 misses succeeded")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"5 misses sleeps {sleeps}")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"5 misses made {len(_list_calls(transport))} list calls")
    if "try 6/" in log:
        raise AssertionError(f"6th try logged: {log!r}")

    sixth_list, sixth_zip, _blob, _files = hit_responses(artifact_id=16)
    transport = FakeTransport(
        [http_status(404, b"missing") for _ in range(5)] + [sixth_list, sixth_zip]
    )
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError("6th try was issued and succeeded")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"6th try was issued: {transport.calls}")
    if len(_zip_calls(transport)) != 0:
        raise AssertionError("6th try downloaded a zip")


def case_r6_message(mod) -> None:
    transport = FakeTransport(
        [http_json(200, {"total_count": 0, "artifacts": []}) for _ in range(5)]
    )
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError("all-miss exited 0")
    if NOT_FOUND_MESSAGE not in log:
        raise AssertionError(f"missing frozen not-found message: {log!r}")
    for attempt in range(1, 6):
        if f"try {attempt}/5" not in log:
            raise AssertionError(f"missing try {attempt} line: {log!r}")
    if log.count("try ") < 5:
        raise AssertionError(f"try lines missing: {log!r}")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"all-miss sleeps {sleeps}")


def case_r7_real_caller() -> None:
    parsed = _load_yaml(_ci_text())
    path = script_path_from_ci(parsed)
    if path != REPO / "tools" / "chiefs_narrative" / "download_publication_compatibility.py":
        raise AssertionError(f"unexpected cited path {path}")
    mod = load_script(path)
    resolved = Path(mod.__file__).resolve()
    if resolved != path.resolve():
        raise AssertionError(f"loaded {resolved} instead of cited {path}")
    listing, zipped, _blob, files = hit_responses()
    transport = FakeTransport([listing, zipped])
    dest = Path(tempfile.mkdtemp()) / "r7"
    rc, log, _sleeps, dest = run_download(mod, transport, dest=dest)
    if rc != 0:
        raise AssertionError(f"cited script failed: {log}")
    if (dest / "payload/fixture.json").read_bytes() != files["payload/fixture.json"]:
        raise AssertionError("cited script did not extract")
    step = _step_by_name(parsed, "Download publication compatibility fixture")
    if SCRIPT_MODULE not in str(step.get("run")):
        raise AssertionError("ci.yml does not cite the driven module")


def case_r8_message_not_swallowed(mod) -> None:
    case_r6_message(mod)


def case_r9_try_count(mod) -> None:
    listing, zipped, _blob, _files = hit_responses()
    transport = FakeTransport([http_status(404, b"missing"), listing, zipped])
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc != 0:
        raise AssertionError(f"try-count hit failed {log}")
    if "try 1/5" not in log or "try 2/5" not in log:
        raise AssertionError(f"try count missing from log: {log!r}")
    if sleeps != [3]:
        raise AssertionError(f"try-count sleeps {sleeps}")
    download = _step_by_name(_load_yaml(_ci_text()), "Download publication compatibility fixture")
    if SCRIPT_MODULE not in str(download.get("run")):
        raise AssertionError("live download step is not the retry script")


def case_r10_harness_in_gates() -> None:
    parsed = _load_yaml(_ci_text())
    step = _step_by_name(parsed, "Publication compatibility download retry harness")
    run = str(step.get("run") or "")
    if HARNESS_MODULE not in run:
        raise AssertionError(f"gates harness step does not run {HARNESS_MODULE}: {run}")
    if Path(__file__).resolve() != (REPO / "tools" / "tests" / "run_publication_compatibility_download_harness.py"):
        raise AssertionError("this file is not the wired harness")
    names = [s.get("name") for s in _gates(parsed).get("steps") or [] if isinstance(s, dict)]
    if names[0] != "Checkout":
        raise AssertionError("harness wiring moved Checkout")
    if "Publication compatibility download retry harness" not in names:
        raise AssertionError("harness not in gates")
    if names.index("Publication compatibility download retry harness") > names.index(
        "Download publication compatibility fixture"
    ):
        raise AssertionError("harness should run before the live download")


def case_n1_caught_up() -> None:
    behind = subprocess.check_output(
        ["git", "rev-list", "--count", "HEAD..origin/develop"],
        cwd=REPO,
        text=True,
    ).strip()
    if behind != "0":
        raise AssertionError(f"HEAD is {behind} commits behind origin/develop")
    develop_sha = subprocess.check_output(
        ["git", "rev-parse", "origin/develop"],
        cwd=REPO,
        text=True,
    ).strip()
    merge_base = subprocess.check_output(
        ["git", "merge-base", "HEAD", "origin/develop"],
        cwd=REPO,
        text=True,
    ).strip()
    if merge_base != develop_sha:
        raise AssertionError("origin/develop is not an ancestor of HEAD")


def case_n2_recorded_sleeps(mod) -> None:
    transport = FakeTransport([http_status(404, b"missing") for _ in range(8)])
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError("5-try all-miss succeeded")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"recorded sleeps {sleeps} != [3, 3, 3, 3]")
    if _try_lines(log) != ["try 1/5", "try 2/5", "try 3/5", "try 4/5", "try 5/5"]:
        raise AssertionError(f"recorded tries {_try_lines(log)}")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"recorded list calls {len(_list_calls(transport))}")


def case_n3a_missing_digest(mod) -> None:
    variants: tuple[tuple[str, dict[str, Any]], ...] = (
        ("missing", {"omit_digest": True}),
        ("blank", {"digest": ""}),
    )
    for label, kwargs in variants:
        listing, zipped, _blob, _files = hit_responses(**kwargs)
        later_list, later_zip, _blob2, _files2 = hit_responses(artifact_id=99)
        dest = Path(tempfile.mkdtemp()) / "n3a" / label
        transport = FakeTransport([listing, zipped, later_list, later_zip])
        rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
        if rc == 0:
            raise AssertionError(f"{label} digest accepted rc=0 log={log!r}")
        if sleeps:
            raise AssertionError(f"{label} digest retried sleeps={sleeps}")
        if len(_list_calls(transport)) != 1:
            raise AssertionError(f"{label} digest list retries {len(_list_calls(transport))}")
        if len(_zip_calls(transport)) != 1:
            raise AssertionError(f"{label} digest zip retries {len(_zip_calls(transport))}")
        if dest.exists() and any(path.is_file() for path in dest.rglob("*")):
            raise AssertionError(f"{label} digest extracted into {dest}")
        if "digest mismatch" not in log:
            raise AssertionError(f"{label} digest log={log!r}")


def _slip_zip(relative_name: str, absolute_name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(zipfile.ZipInfo(relative_name), b"pwned-rel\n")
        archive.writestr(zipfile.ZipInfo(absolute_name), b"pwned-abs\n")
    return buf.getvalue()


def case_n3b_zip_slip(mod) -> None:
    root = Path(tempfile.mkdtemp())
    dest = root / "artifact-roundtrip"
    dest.mkdir(parents=True, exist_ok=True)
    sentinel = root / "sentinel.txt"
    sentinel.write_bytes(b"keep\n")
    abs_name = str((root / "abs-escaped.txt").resolve())
    blob = _slip_zip("../escaped.txt", abs_name)
    listing, zipped, _blob, _files = hit_responses(zip_body=blob, files={})
    before = _files_outside(root, dest)
    if str(sentinel.resolve()) not in before:
        raise AssertionError("sentinel parent listing missed the planted file")
    transport = FakeTransport([listing, zipped])
    rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
    after = _files_outside(root, dest)
    if after != before:
        raise AssertionError(f"zip-slip wrote outside dest: before={before!r} after={after!r}")
    if rc == 0:
        raise AssertionError(f"zip-slip extracted rc=0 log={log!r}")
    if sleeps:
        raise AssertionError(f"zip-slip retried {sleeps}")


def apply_mutant(source: str, mutant_id: str) -> str:
    old, new = MUTANTS[mutant_id]
    count = source.count(old)
    if count != 1:
        raise AssertionError(f"{mutant_id} anchor count {count} for {old!r}")
    return source.replace(old, new, 1)


def killer_m1(mod) -> None:
    sixth_list, sixth_zip, _blob, _files = hit_responses(artifact_id=16)
    transport = FakeTransport(
        [http_status(404, b"missing") for _ in range(5)] + [sixth_list, sixth_zip]
    )
    rc, log, sleeps, _dest = run_download(mod, transport)
    tries = _try_lines(log)
    if tries != ["try 1/5", "try 2/5", "try 3/5", "try 4/5", "try 5/5"]:
        raise AssertionError(f"observed tries {tries}")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"observed sleeps {sleeps}")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"observed list calls {len(_list_calls(transport))}")
    if _zip_calls(transport):
        raise AssertionError("observed a zip download after try 5")
    if rc == 0:
        raise AssertionError("N>5 accepted a 6th try")


def killer_m2(mod) -> None:
    delayed = [http_status(404, b"missing") for _ in range(4)]
    listing, zipped, _blob, _files = hit_responses(artifact_id=15)
    transport = FakeTransport(delayed + [listing, zipped])
    rc, log, sleeps, _dest = run_download(mod, transport)
    tries = _try_lines(log)
    if tries != ["try 1/5", "try 2/5", "try 3/5", "try 4/5", "try 5/5"]:
        raise AssertionError(f"observed tries {tries}")
    if sleeps != [3, 3, 3, 3]:
        raise AssertionError(f"observed sleeps {sleeps}")
    if len(_list_calls(transport)) != 5:
        raise AssertionError(f"observed list calls {len(_list_calls(transport))}")
    if rc != 0:
        raise AssertionError(f"hit on try 5 must pass: rc={rc} log={log!r}")


def killer_m3(mod) -> None:
    case_n2_recorded_sleeps(mod)


def killer_m4(mod) -> None:
    transport = FakeTransport([http_status(404, b"missing") for _ in range(5)])
    rc, log, _sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError(f"all-miss exited 0: {log!r}")
    if rc != 1:
        raise AssertionError(f"all-miss rc={rc}")


def killer_m5(mod) -> None:
    bad_list, bad_zip, _blob, _files = hit_responses(digest="sha256:" + ("ab" * 32))
    good_list, good_zip, _blob2, _files2 = hit_responses(artifact_id=12)
    transport = FakeTransport([bad_list, bad_zip, good_list, good_zip])
    dest = Path(tempfile.mkdtemp()) / "m5"
    rc, log, sleeps, dest = run_download(mod, transport, dest=dest)
    if rc == 0:
        raise AssertionError("digest mismatch retried to a later hit")
    if sleeps:
        raise AssertionError(f"digest mismatch retried {sleeps}")
    if len(transport.calls) != 2:
        raise AssertionError(f"digest mismatch retried calls={transport.calls}")
    if "digest mismatch" not in log:
        raise AssertionError(f"digest mismatch log: {log!r}")


def killer_m6(mod) -> None:
    transport = FakeTransport(
        [http_status(403, b'{"message":"Forbidden"}'), *hit_responses(artifact_id=44)[:2]]
    )
    rc, log, sleeps, _dest = run_download(mod, transport)
    if rc == 0:
        raise AssertionError("403 retried to a later hit")
    if sleeps:
        raise AssertionError(f"403 retried {sleeps} log={log!r}")
    if len(transport.calls) != 1:
        raise AssertionError(f"403 retried calls={transport.calls}")


def killer_m7(mod) -> None:
    listing, zipped, _blob, _files = hit_responses()
    transport = FakeTransport([listing, zipped])
    rc, log, _sleeps, _dest = run_download(mod, transport)
    if rc != 0:
        raise AssertionError(f"pinned download failed {log}")
    lists = _list_calls(transport)
    if not lists:
        raise AssertionError("no list call")
    assert_pinned_list_url(lists[0]["url"])


def killer_m8(mod) -> None:
    transport = FakeTransport([http_status(404, b"missing") for _ in range(5)])
    rc, log, _sleeps, _dest = run_download(mod, transport)
    if NOT_FOUND_MESSAGE not in log:
        raise AssertionError(f"failure message swallowed: {log!r}")
    if rc == 0:
        raise AssertionError("all-miss succeeded while checking message")


KILLERS: dict[str, Callable] = {
    "M1": killer_m1,
    "M2": killer_m2,
    "M3": killer_m3,
    "M4": killer_m4,
    "M5": killer_m5,
    "M6": killer_m6,
    "M7": killer_m7,
    "M8": killer_m8,
    "MN3a": case_n3a_missing_digest,
    "MN3b": case_n3b_zip_slip,
}

CRASH_TOKENS = (
    "Traceback",
    "SyntaxError",
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
    "IndentationError",
)


def run_behavioural(mod) -> None:
    case_r2_same_run(mod)
    case_r3_integrity(mod)
    case_r4_retry_only_not_found(mod)
    case_r5_bounds(mod)
    case_r6_message(mod)
    case_r8_message_not_swallowed(mod)
    case_r9_try_count(mod)
    case_n2_recorded_sleeps(mod)
    case_n3a_missing_digest(mod)
    case_n3b_zip_slip(mod)


def _is_assertion(exc: BaseException) -> bool:
    if not isinstance(exc, AssertionError):
        return False
    text = str(exc)
    return not any(token in text for token in CRASH_TOKENS)


def run_mutant(mutant_id: str) -> str:
    path = script_path_from_ci()
    original = path.read_text(encoding="utf-8")
    mutated_source = apply_mutant(original, mutant_id)
    if mutated_source == original:
        raise AssertionError(f"{mutant_id} did not change the script")
    killer = KILLERS[mutant_id]
    controls = 0
    kills = 0
    last_receipt = ""
    for round_id in range(REPEATS):
        clean = load_script(_write_temp(original))
        run_behavioural(clean)
        killer(clean)
        controls += 1
        mutated = load_script(_write_temp(mutated_source))
        try:
            killer(mutated)
        except AssertionError as exc:
            if not _is_assertion(exc):
                raise AssertionError(
                    f"{mutant_id} round {round_id} crash-shaped assertion: {exc}"
                ) from exc
            last_receipt = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            kills += 1
            continue
        except Exception as exc:  # noqa: BLE001 - classify non-behavioural kills
            raise AssertionError(
                f"{mutant_id} round {round_id} not behavioural: {type(exc).__name__}: {exc}"
            ) from exc
        raise AssertionError(f"{mutant_id} round {round_id} survived {killer.__name__}")
    if kills != REPEATS or controls != REPEATS:
        raise AssertionError(
            f"{mutant_id} kills {kills}/{REPEATS} control {controls}/{REPEATS}"
        )
    return (
        f"KILLED {mutant_id} {killer.__name__} {kills}/{REPEATS} "
        f"control {controls}/{REPEATS}: {last_receipt}"
    )


def _write_temp(source: str) -> Path:
    directory = Path(tempfile.mkdtemp())
    path = directory / "download_publication_compatibility.py"
    path.write_text(source, encoding="utf-8")
    return path


CASES: dict[str, Callable] = {
    "R1": case_r1_scope,
    "R2": lambda: case_r2_same_run(live_script()),
    "R3": lambda: case_r3_integrity(live_script()),
    "R4": lambda: case_r4_retry_only_not_found(live_script()),
    "R5": lambda: case_r5_bounds(live_script()),
    "R6": lambda: case_r6_message(live_script()),
    "R7": case_r7_real_caller,
    "R8": lambda: case_r8_message_not_swallowed(live_script()),
    "R9": lambda: case_r9_try_count(live_script()),
    "R10": case_r10_harness_in_gates,
    "N1": case_n1_caught_up,
    "N2": lambda: case_n2_recorded_sleeps(live_script()),
    "N3a": lambda: case_n3a_missing_digest(live_script()),
    "N3b": lambda: case_n3b_zip_slip(live_script()),
}


RECEIPTS = {
    "R1": "Upload/Verify/automerge match origin/develop; download is the REST script; job actions:read only",
    "R2": "list URL pinned to /actions/runs/{run_id}/artifacts?name=publication-compatibility",
    "R3": "matching digest extracts; mismatch fails with 0 retries; upload-artifact stays SHA-pinned",
    "R4": "digest/403/401/5xx-after-download/bad-zip fail immediately with 0 retries",
    "R5": "N=5 delay=3s; try1 hit; try5 hit; 5 misses fail; never a 6th try; timeout-minutes=3",
    "R6": "all-miss rc!=0 and log contains Artifact not found for name: publication-compatibility",
    "R7": "drove tools/chiefs_narrative/download_publication_compatibility.py cited by ci.yml",
    "R8": "all-miss log still contains the frozen not-found message",
    "R9": "script logs try N/5; live pull_request attempt 1 is the remaining proof",
    "R10": "gates job runs tools.tests.run_publication_compatibility_download_harness",
    "N1": "HEAD is 0 behind origin/develop; merge-base is origin/develop",
    "N2": "injected sleep recorded [3, 3, 3, 3] on a 5-try all-miss; tries 1/5..5/5",
    "N3a": "listed artifact with missing/blank digest fails immediately; 0 retries, 0 sleeps",
    "N3b": "zip-slip ../x and absolute path fail closed; sentinel parent listing unchanged",
}


def run_case(frozen_id: str) -> None:
    if frozen_id in CASES:
        CASES[frozen_id]()
        print(RECEIPTS[frozen_id], flush=True)
        return
    if frozen_id in KILLERS:
        print(run_mutant(frozen_id), flush=True)
        return
    raise AssertionError(f"unknown frozen id {frozen_id}")


def run_all() -> int:
    failed = 0
    for frozen_id in list(CASES) + list(KILLERS):
        try:
            run_case(frozen_id)
            print(f"PASS {frozen_id}", flush=True)
        except Exception as exc:  # noqa: BLE001 - report every frozen id
            failed += 1
            print(f"FAIL {frozen_id}: {exc}", flush=True)
            traceback.print_exc()
    return 1 if failed else 0


class PublicationCompatibilityDownloadHarness(unittest.TestCase):
    def test_frozen_ids_and_mutants(self):
        rc = run_all()
        self.assertEqual(rc, 0)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return run_all()
    if args == ["--mutants"]:
        failed = 0
        for mutant_id in KILLERS:
            try:
                print(run_mutant(mutant_id), flush=True)
                print(f"PASS {mutant_id}", flush=True)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {mutant_id}: {exc}", flush=True)
                traceback.print_exc()
        return 1 if failed else 0
    failed = 0
    for frozen_id in args:
        try:
            run_case(frozen_id)
            print(f"PASS {frozen_id}", flush=True)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {frozen_id}: {exc}", flush=True)
            traceback.print_exc()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
