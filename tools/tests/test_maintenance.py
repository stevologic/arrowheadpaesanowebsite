"""Regression coverage for article routing, transport failures, and no-op slates."""
import copy
import json
import os
import re
import subprocess
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import yaml

from tools.chiefs_narrative import collect, generate, publication_queue, providers


class ArticleRouting(unittest.TestCase):
    def setUp(self):
        providers._latest_grok_model.cache_clear()

    def tearDown(self):
        providers._latest_grok_model.cache_clear()

    def test_latest_uses_account_visible_numbered_text_model_once(self):
        models = ["grok-4.3", "grok-4.7", "grok-4.8", "grok-4.20-0309-reasoning",
                  "grok-4.20-multi-agent-0309", "grok-imagine-image", "grok-build-0.1"]
        response = Mock(status_code=200)
        response.json.return_value = {"data": [{"id": model} for model in models]}
        env = {"GROK_MODEL": "latest", "XAI_API_KEY": "test-key",
               "XAI_BASE_URL": "https://example.test/v1"}
        with patch.dict("os.environ", env, clear=True), \
             patch.object(providers.requests, "get", return_value=response) as get:
            self.assertEqual(providers.grok_model(), "grok-4.8")
            self.assertEqual(providers.grok_model(), "grok-4.8")
        get.assert_called_once_with("https://example.test/v1/models",
                                    headers={"Authorization": "Bearer test-key"},
                                    timeout=(20, 20))
        response.close.assert_called_once()

    def test_missing_or_failed_latest_discovery_never_uses_an_older_model(self):
        for status, data in [(200, {"data": [{"id": "grok-4.6"}]}),
                             (403, {}), (200, {"data": None})]:
            providers._latest_grok_model.cache_clear()
            response = Mock(status_code=status)
            response.json.return_value = data
            with self.subTest(status=status, data=data), \
                 patch.dict("os.environ", {"GROK_MODEL": "latest", "GROK_API_KEY": "test"}, clear=True), \
                 patch.object(providers.requests, "get", return_value=response), \
                 self.assertRaises(providers.ProviderError):
                providers.grok_model()
            response.close.assert_called_once()

    def test_timeout_does_not_call_configured_fallback_models(self):
        env = {"GROK_MODEL": "grok-4.7", "GROK_FALLBACK_MODELS": "grok-4.6,grok-4.5"}
        with patch.dict("os.environ", env, clear=True), \
             patch.object(providers, "_chat_completions", side_effect=providers.ProviderError("deadline")) as call, \
             self.assertRaises(providers.ProviderError):
            providers.generate_via_llm("grok", "system", "user")
        self.assertEqual(call.call_count, 1)
        self.assertEqual(call.call_args.args[3], "grok-4.7")

    def test_selected_model_is_recorded_on_success(self):
        with patch.dict("os.environ", {"GROK_MODEL": "grok-4.7"}, clear=True), \
             patch.object(providers, "_chat_completions", return_value='{"headline":"test"}'):
            draft, label = providers.generate_via_llm("xai", "system", "user")
        self.assertEqual(label, "grok:grok-4.7")
        self.assertEqual(draft["headline"], "test")

    def test_grok_error_never_calls_offline_writer(self):
        for name in ("grok", "xai"):
            with self.subTest(name=name), \
                 patch.object(providers, "generate_via_llm", side_effect=providers.ProviderError("timeout")), \
                 patch.object(generate.offline, "write") as offline, \
                 self.assertRaises(providers.ProviderError):
                generate._draft_raw(name, "system", "user", {}, {}, [])
            offline.assert_not_called()

    def test_cli_provider_failure_preserves_published_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [root / name for name in ("narrative.json", "archive.json", "repair.json")]
            for path in paths:
                path.write_bytes(b'{"keep":"published"}\r\n')
            with patch.object(generate.config, "NARRATIVE_JSON", paths[0]), \
                 patch.object(generate.config, "ARCHIVE_JSON", paths[1]), \
                 patch.object(generate.config, "REPAIR_JSON", paths[2]), \
                 patch.object(generate, "build", side_effect=providers.ProviderError("timeout")):
                self.assertEqual(generate.main(["--provider", "grok"]), 1)
            self.assertTrue(all(path.read_bytes() == b'{"keep":"published"}\r\n' for path in paths))


class WriterTransport(unittest.TestCase):
    def response(self, lines, status=200, text=""):
        response = Mock(status_code=status, text=text)
        response.iter_lines.return_value = iter(lines)
        return response

    def test_400_model_error_is_not_retried(self):
        response = self.response([], status=400, text="model unavailable")
        with patch.object(providers.requests, "post", return_value=response) as post, \
             self.assertRaises(providers.ProviderError):
            providers._chat_completions("Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True)
        self.assertEqual(post.call_count, 1)
        response.close.assert_called_once()

    def test_only_explicit_response_format_error_retries_once(self):
        rejected = self.response([], status=400, text="response_format not supported")
        accepted = self.response(['data: {"choices":[{"delta":{"content":"{}"}}]}', 'data: [DONE]'])
        payloads = []

        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs["json"]))
            return rejected if len(payloads) == 1 else accepted

        with patch.object(providers.requests, "post", side_effect=post):
            self.assertEqual(providers._chat_completions("Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True), "{}")
        self.assertEqual(len(payloads), 2)
        self.assertIn("response_format", payloads[0])
        self.assertNotIn("response_format", payloads[1])
        self.assertEqual(payloads[1]["stream_options"], {"include_usage": True})
        rejected.close.assert_called_once()
        accepted.close.assert_called_once()

    def test_stream_payload_sends_reasoning_effort(self):
        payloads = []

        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs["json"]))
            return self.response(['data: {"choices":[{"delta":{"content":"{}"}}]}', 'data: [DONE]'])

        with patch.dict("os.environ", {"GROK_REASONING_EFFORT": ""}, clear=False), \
             patch.object(providers.requests, "post", side_effect=post):
            self.assertEqual(
                providers._chat_completions(
                    "Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True
                ),
                "{}",
            )
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]["reasoning_effort"], "low")

        payloads.clear()
        with patch.dict("os.environ", {"GROK_REASONING_EFFORT": "medium"}, clear=False), \
             patch.object(providers.requests, "post", side_effect=post):
            providers._chat_completions(
                "Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True
            )
        self.assertEqual(payloads[0]["reasoning_effort"], "medium")

    def test_stream_payload_includes_max_tokens(self):
        payloads = []

        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs["json"]))
            return self.response(['data: {"choices":[{"delta":{"content":"{}"}}]}', 'data: [DONE]'])

        with patch.dict("os.environ", {"LLM_MAX_TOKENS": ""}, clear=False), \
             patch.object(providers.requests, "post", side_effect=post):
            self.assertEqual(
                providers._chat_completions(
                    "Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True
                ),
                "{}",
            )
        self.assertEqual(payloads[0]["max_tokens"], 16384)
        self.assertIn("stream", payloads[0])

    def test_reasoning_effort_400_retries_without_it(self):
        rejected = self.response([], status=400, text="reasoning_effort is not supported")
        accepted = self.response(['data: {"choices":[{"delta":{"content":"{}"}}]}', 'data: [DONE]'])
        payloads = []

        def post(*args, **kwargs):
            payloads.append(copy.deepcopy(kwargs["json"]))
            return rejected if len(payloads) == 1 else accepted

        with patch.object(providers.requests, "post", side_effect=post):
            self.assertEqual(
                providers._chat_completions(
                    "Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True
                ),
                "{}",
            )
        self.assertEqual(len(payloads), 2)
        self.assertEqual(payloads[0]["reasoning_effort"], "low")
        self.assertNotIn("reasoning_effort", payloads[1])
        self.assertIn("response_format", payloads[1])
        rejected.close.assert_called_once()
        accepted.close.assert_called_once()

    def test_partial_or_truncated_stream_is_rejected_and_closed(self):
        content = 'data: {"choices":[{"delta":{"content":"{}"}}]}'
        for ending in ([], ['data: {"choices":[{"finish_reason":"length"}]}', 'data: [DONE]'],
                       ['data: {"error":{"message":"upstream failed"}}']):
            response = self.response([content, *ending])
            with self.subTest(ending=ending), \
                 patch.object(providers.requests, "post", return_value=response), \
                 self.assertRaises(providers.ProviderError):
                providers._chat_completions("Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True)
            response.close.assert_called_once()

    def test_usage_is_reported_without_extra_request(self):
        response = self.response(['data: {"choices":[{"delta":{"content":"{}"},"finish_reason":"stop"}]}',
                                  'data: {"choices":[],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}',
                                  'data: [DONE]'])
        with patch.object(providers.requests, "post", return_value=response) as post, \
             patch("builtins.print") as log:
            self.assertEqual(providers._chat_completions("Grok", "https://example.test/v1", "test", "grok-4.7", "s", "u", stream=True), "{}")
        self.assertEqual(post.call_count, 1)
        logged = " ".join(str(call.args[0]) for call in log.call_args_list if call.args)
        self.assertIn('"total_tokens": 30', logged)
        self.assertIn("ttft=", logged)
        self.assertIn("elapsed=", logged)
        self.assertIn("bytes=", logged)

    def test_deadline_fires_during_silent_stream(self):
        class SilentResp:
            def __init__(self):
                self.closed = threading.Event()

            def iter_lines(self, decode_unicode=True, chunk_size=512):
                self.closed.wait(timeout=30)
                return
                yield

            def close(self):
                self.closed.set()

        with patch.object(providers, "llm_deadline", return_value=0.3), \
             patch("builtins.print") as log, \
             self.assertRaises(providers.ProviderError) as ctx:
            providers._read_stream("Grok", SilentResp())
        self.assertIn("deadline", str(ctx.exception))
        logged = " ".join(str(call.args[0]) for call in log.call_args_list if call.args)
        self.assertIn("ttft=none", logged)
        self.assertIn("elapsed=", logged)
        self.assertIn("bytes=0", logged)

    def test_deadline_fires_on_real_keepalive_socket(self):
        """Keep-alives must not let close() or a 512-byte buffer outrun the cap."""
        for interval in (0.5, 2.0):
            with self.subTest(interval=interval):
                self._assert_deadline_on_keepalive_socket(interval)

    def _assert_deadline_on_keepalive_socket(self, interval: float) -> None:
        stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        if stop.wait(interval):
                            return
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def log_message(self, format, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server = threading.Thread(target=httpd.serve_forever, daemon=True)
        server.start()
        deadline = 3
        box: dict = {}

        def run() -> None:
            try:
                providers._chat_completions(
                    "Grok",
                    f"http://127.0.0.1:{httpd.server_address[1]}/v1",
                    "test",
                    "grok-4.7",
                    "s",
                    "u",
                    stream=True,
                )
            except Exception as exc:
                box["exc"] = exc

        try:
            with patch.dict("os.environ", {"LLM_DEADLINE": str(deadline), "LLM_TIMEOUT": "25"}, clear=False), \
                 patch("builtins.print") as log:
                started = time.monotonic()
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                worker.join(timeout=deadline + 1)
                elapsed = time.monotonic() - started
                self.assertFalse(worker.is_alive(), f"deadline overshot ({elapsed:.1f}s) at interval={interval}")
                self.assertLess(elapsed, deadline + 1)
                self.assertIsInstance(box.get("exc"), providers.ProviderError)
                self.assertIn("deadline", str(box["exc"]))
                logged = " ".join(str(call.args[0]) for call in log.call_args_list if call.args)
                match = re.search(r"bytes=(\d+)", logged)
                self.assertIsNotNone(match, logged)
                self.assertGreater(int(match.group(1)), 0, logged)
        finally:
            stop.set()
            httpd.shutdown()
            httpd.server_close()

    def test_deadline_includes_connect_and_header_wait(self):
        """Header delay plus keep-alives share one deadline, not deadline+deadline."""
        stop = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                time.sleep(2)
                if stop.is_set():
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while not stop.is_set():
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        if stop.wait(0.5):
                            return
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

            def log_message(self, format, *args):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        deadline = 3
        box: dict = {}

        def run() -> None:
            try:
                providers._chat_completions(
                    "Grok",
                    f"http://127.0.0.1:{httpd.server_address[1]}/v1",
                    "test",
                    "grok-4.7",
                    "s",
                    "u",
                    stream=True,
                )
            except Exception as exc:
                box["exc"] = exc

        try:
            with patch.dict("os.environ", {"LLM_DEADLINE": str(deadline), "LLM_TIMEOUT": "25"}, clear=False):
                started = time.monotonic()
                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                worker.join(timeout=deadline + 1)
                elapsed = time.monotonic() - started
                self.assertFalse(worker.is_alive(), f"header wait stacked another deadline ({elapsed:.1f}s)")
                self.assertLess(elapsed, deadline + 1)
                self.assertIsInstance(box.get("exc"), providers.ProviderError)
                self.assertIn("deadline", str(box["exc"]))
        finally:
            stop.set()
            httpd.shutdown()
            httpd.server_close()


class ScheduleIdempotency(unittest.TestCase):
    def test_same_data_preserves_bytes_and_real_changes_are_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "schedule.json"
            original = b'[{"tv":"CBS","id":"1","kcScore":null}]\r\n'
            path.write_bytes(original)
            games = [{"id": "1", "kcScore": None, "tv": "CBS"}]
            with patch.object(collect.config, "SCHEDULE_JSON", path), \
                 patch.object(collect.config, "ensure_dirs"):
                self.assertTrue(collect.write_schedule(games))
                self.assertEqual(path.read_bytes(), original)
                games[0]["kcScore"] = 30
                self.assertTrue(collect.write_schedule(games))
                self.assertEqual(json.loads(path.read_text())[0]["kcScore"], 30)

    def test_schedule_only_never_calls_writer_or_markets(self):
        with patch.object(collect, "refresh_schedule", return_value=[{"id": "1"}]), \
             patch.object(providers, "generate_via_llm") as writer, \
             patch.object(generate.odds, "collect_markets") as markets:
            self.assertEqual(generate.main(["--schedule-only"]), 0)
        writer.assert_not_called()
        markets.assert_not_called()


class MaintenancePolicy(unittest.TestCase):
    @staticmethod
    def _merge_condition(workflow, **overrides):
        """Evaluate these trusted workflow guards against event scenarios.

        The two guard expressions use only comparisons, boolean operators,
        contains/startsWith, and JSON arrays; no runner code is executed.
        """
        settings = dict(base="main", actor="stevologic", author="stevologic",
                        draft=False, labels=[], association="OWNER",
                        head_ref="feature/preview",
                        head_repo="stevologic/arrowheadpaesanowebsite")
        settings.update(overrides)
        pr = SimpleNamespace(
            user=SimpleNamespace(login=settings["author"]),
            draft=settings["draft"], author_association=settings["association"],
            head=SimpleNamespace(repo=SimpleNamespace(full_name=settings["head_repo"])),
        )
        event_name = "pull_request_target" if "pull_request_target" in workflow["on"] else "pull_request"
        github = SimpleNamespace(
            base_ref=settings["base"], actor=settings["actor"], event_name=event_name,
            head_ref=settings["head_ref"], repository="stevologic/arrowheadpaesanowebsite",
            event=SimpleNamespace(pull_request=pr),
        )
        expression = workflow["jobs"]["automerge"]["if"].strip()[3:-2].strip()
        expression = expression.replace("github.event.pull_request.labels.*.name", "labels")
        expression = expression.replace("&&", " and ").replace("||", " or ")
        expression = re.sub(r"!(?!=)", "not ", expression)
        expression = re.sub(r"\bfalse\b", "False", expression)
        functions = {"__builtins__": {}, "fromJSON": json.loads,
                     "contains": lambda items, value: value in items,
                     "startsWith": lambda value, prefix: value.startswith(prefix)}
        return eval(" ".join(expression.split()), functions,
                    {"github": github, "labels": settings["labels"]})

    def test_both_merge_jobs_reject_integration_and_dependabot_events(self):
        root = Path(__file__).resolve().parents[2]
        scenarios = [
            ({}, True),  # Eligible main publication still enters the content gate.
            ({"base": "develop"}, False),
            ({"head_ref": "develop"}, False),  # Promotion preserves integration ancestry.
            ({"actor": "dependabot[bot]"}, False),
            ({"author": "dependabot[bot]"}, False),  # Human-triggered bot PR update.
            ({"actor": "github-actions[bot]"}, False),
            ({"author": "github-actions[bot]"}, False),
            ({"draft": True}, False),
            ({"labels": ["hold"]}, False),
            ({"head_repo": "someone/arrowheadpaesanowebsite"}, False),
        ]
        for filename in ("ci.yml", "edition-qa.yml"):
            doc = yaml.load((root / ".github/workflows" / filename).read_text(), Loader=yaml.BaseLoader)
            for event, expected in scenarios:
                with self.subTest(workflow=filename, event=event):
                    self.assertEqual(self._merge_condition(doc, **event), expected)
            job = doc["jobs"]["automerge"]
            self.assertEqual(job["needs"], "edition-gates" if filename == "edition-qa.yml" else "gates")
            scripts = "\n".join(step.get("run", "") for step in job["steps"])
            self.assertIn("GATE_BASE", scripts)
            self.assertIn("review_gate --automerge", scripts)
            self.assertNotIn("--delete-branch", scripts)

    def test_integration_tests_without_deployment_or_automerge(self):
        root = Path(__file__).resolve().parents[2]
        # BaseLoader preserves the key 'on' (YAML 1.1 treats it as a boolean).
        ci = yaml.load((root / ".github/workflows/ci.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertIn("develop", ci["on"]["pull_request"]["branches"])
        self.assertEqual(ci["on"]["push"]["branches"], ["develop"])
        self.assertIn("github.base_ref == 'main'", ci["jobs"]["automerge"]["if"])
        self.assertIn("github.event.pull_request.user.login != 'dependabot[bot]'", ci["jobs"]["automerge"]["if"])
        pages = yaml.load((root / ".github/workflows/pages.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(pages["on"]["push"]["branches"], ["main"])

    def test_dependency_versions_target_integration_and_security_default(self):
        path = Path(__file__).resolve().parents[2] / ".github/dependabot.yml"
        updates = yaml.safe_load(path.read_text())["updates"]
        regular = [row for row in updates if row.get("target-branch")]
        self.assertEqual({row["package-ecosystem"] for row in regular}, {"pip", "npm", "github-actions"})
        for row in regular:
            self.assertEqual(row["target-branch"], "develop")
            self.assertLessEqual(row["open-pull-requests-limit"], 2)
        for row in updates:
            if "target-branch" not in row:
                self.assertEqual(row["open-pull-requests-limit"], 0)
                self.assertTrue(all(group["applies-to"] == "security-updates" for group in row["groups"].values()))


class PublicationPolicy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[2] / ".github/workflows"
        cls.narrative = yaml.load((root / "narrative.yml").read_text(), Loader=yaml.BaseLoader)
        cls.ci = yaml.load((root / "ci.yml").read_text(), Loader=yaml.BaseLoader)

    def test_required_pr_gate_cannot_skip_bot_or_edition_events(self):
        trigger = self.ci["on"]["pull_request"]
        self.assertEqual(trigger["branches"], ["main", "develop"])
        self.assertNotIn("paths-ignore", trigger)
        self.assertNotIn("paths", trigger)
        self.assertNotIn("if", self.ci["jobs"]["gates"])
        self.assertEqual(self.ci["permissions"], {"contents": "read"})
        jobs = self.narrative["jobs"]
        self.assertNotIn("deploy", jobs)
        self.assertEqual(jobs["update"]["permissions"], {"contents": "write", "pull-requests": "write"})
        scripts = "\n".join(step.get("run", "") for job in jobs.values() for step in job["steps"])
        for forbidden in ("gh workflow run", "gh pr merge", "gh run approve", "sleep 5", "--delete-branch"):
            self.assertNotIn(forbidden, scripts)
        self.assertIn("--draft", scripts)
        self.assertIn('BASE="develop"', scripts)

    def test_queue_blocks_provider_and_duplicate_pr_work(self):
        steps = self.narrative["jobs"]["generate"]["steps"]
        queue = next(step for step in steps if step.get("id") == "queue")
        writer = next(step for step in steps if step["name"] == "Generate the Chiefs Narrative")
        slate = next(step for step in steps if step["name"] == "Refresh the published slate")
        self.assertLess(steps.index(queue), steps.index(writer))
        self.assertIn("publication_queue", queue["run"])
        for pending, expected in (("true", False), ("false", True)):
            namespace = SimpleNamespace(
                queue=SimpleNamespace(outputs=SimpleNamespace(pending=pending)),
                live=SimpleNamespace(outputs=SimpleNamespace(skip="false")),
                mode=SimpleNamespace(outputs=SimpleNamespace(mode="full")))
            for step in (writer, slate):
                expression = step["if"].replace("&&", " and ")
                self.assertEqual(eval(expression, {"__builtins__": {}}, {"steps": namespace}), expected)
        detector = next(step for step in steps if step.get("id") == "diff")["run"]
        self.assertIn('steps.queue.outputs.pending', detector)
        self.assertLess(detector.index('echo "changed=false"'), detector.index("git add"))

    @staticmethod
    def _fake_commands(root):
        """Run the actual workflow shell offline and record its git/gh calls."""
        directory = root / "bin"
        directory.mkdir()
        program = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
command = Path(sys.argv[0]).name
record = {'command': command, 'args': sys.argv[1:]}
if '--body-file' in sys.argv:
    record['body'] = Path(sys.argv[sys.argv.index('--body-file') + 1]).read_text(encoding='utf-8')
with open(os.environ['CALLS'], 'a', encoding='utf-8') as out:
    out.write(json.dumps(record) + '\\n')
if command == 'gh':
    if sys.argv[1] == 'api':
        print(os.environ.get('API_RESULT', ''))
        sys.exit(int(os.environ.get('API_EXIT', '0')))
    if sys.argv[1:3] == ['pr', 'create']:
        print('https://github.com/example/site/pull/1')
    elif sys.argv[1:3] != ['pr', 'edit']:
        sys.exit(42)
'''
        for name in ("git", "gh"):
            path = directory / name
            path.write_text(program, encoding="utf-8")
            path.chmod(0o755)
        import shutil
        real_python = shutil.which("python3") or shutil.which("python")
        if real_python:
            for name in ("python", "python3"):
                alias = directory / name
                if not alias.exists():
                    alias.symlink_to(real_python)
        return dict(os.environ, PATH=f"{directory}:{os.environ['PATH']}", CALLS=str(root / "calls.jsonl"),
                    GITHUB_REPOSITORY="example/site", GH_TOKEN="offline-fixture",
                    GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2", RUNNER_TEMP=str(root),
                    GITHUB_OUTPUT=str(root / "output"), GITHUB_STEP_SUMMARY=str(root / "summary"))

    def test_stale_held_publication_pr_does_not_block_next_desk(self):
        """24h / previous CT day PRs stay open but do not skip generation.

        Closing narrative/update-* would violate maintenance.json
        (close/merge/delete: false). Ignoring stale drafts is the safe option.
        """
        now = datetime(2026, 10, 15, 9, 37, tzinfo=timezone.utc)
        fresh = {
            "number": 215,
            "created_at": "2026-10-14T17:50:29Z",
            "head": {"ref": "narrative/update-38608196264-1"},
            "base": {"ref": "develop"},
        }
        stale_hours = {
            "number": 214,
            "created_at": "2026-10-13T09:00:00Z",
            "head": {"ref": "narrative/update-38500000000-1"},
            "base": {"ref": "develop"},
        }
        yesterday_evening = {
            "number": 213,
            "created_at": "2026-10-14T02:00:00Z",
            "head": {"ref": "narrative/update-38400000000-1"},
            "base": {"ref": "develop"},
        }
        other = {
            "number": 96,
            "created_at": "2026-10-15T08:00:00Z",
            "head": {"ref": "codex/other"},
            "base": {"ref": "develop"},
        }
        self.assertFalse(publication_queue.is_blocking_publication_pr(fresh, now=now))
        self.assertFalse(publication_queue.is_blocking_publication_pr(stale_hours, now=now))
        self.assertFalse(
            publication_queue.is_blocking_publication_pr(yesterday_evening, now=now)
        )
        self.assertFalse(publication_queue.is_blocking_publication_pr(other, now=now))
        self.assertEqual(
            publication_queue.pending_publication_numbers(
                [fresh, stale_hours, yesterday_evening, other], now=now
            ),
            [],
        )
        same_afternoon = datetime(2026, 10, 14, 18, 0, tzinfo=timezone.utc)
        self.assertEqual(
            publication_queue.pending_publication_numbers([fresh], now=same_afternoon),
            [215],
        )

    @unittest.skipIf(os.name == "nt", "Actual Actions bash execution is covered on Linux CI")
    def test_queue_shell_is_fail_closed_and_reports_backpressure(self):
        script = next(step["run"] for step in self.narrative["jobs"]["generate"]["steps"] if step.get("id") == "queue")
        fresh = [{
            "number": 215,
            "created_at": "2026-10-14T17:50:29Z",
            "head": {"ref": "narrative/update-38608196264-1"},
            "base": {"ref": "develop"},
        }]
        stale = [{
            "number": 214,
            "created_at": "2026-10-13T09:00:00Z",
            "head": {"ref": "narrative/update-38500000000-1"},
            "base": {"ref": "develop"},
        }]
        cases = (
            ("[]", 0, False),
            (json.dumps(fresh), 0, True),
            (json.dumps(stale), 0, False),
            ("", 42, None),
        )
        for response, exit_code, pending in cases:
            with self.subTest(response=response, exit_code=exit_code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                env = self._fake_commands(root)
                env.update(API_RESULT=response, API_EXIT=str(exit_code))
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=Path(__file__).resolve().parents[2],
                    env=env,
                    capture_output=True,
                    text=True,
                )
                if exit_code:
                    self.assertNotEqual(result.returncode, 0)
                    output = root / "output"
                    self.assertFalse(output.exists() and "pending=false" in output.read_text())
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(
                        (root / "output").read_text().strip(),
                        f"pending={'true' if pending else 'false'}",
                    )

    @unittest.skipIf(os.name == "nt", "Actual Actions bash execution is covered on Linux CI")
    def test_publisher_shell_always_queues_draft_and_preserves_holds(self):
        script = next(step["run"] for step in self.narrative["jobs"]["update"]["steps"] if step.get("id") == "publish")
        for mode in ("schedule", "full"):
            for hold in (False, True):
                with self.subTest(mode=mode, hold=hold), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    env = self._fake_commands(root)
                    env["DESK_MODE"] = mode
                    metadata = dict(holdMerge=hold, dropBody="QA details stay visible", edition="Current edition",
                                    headline="Chiefs 'quotes' $(touch PWNED) stay data")
                    (root / ".narrative-publish.json").write_text(json.dumps(metadata), encoding="utf-8")
                    fixture_data = root / "data"
                    fixture_data.mkdir()
                    # Only this disposable fixture; never read the live edition.
                    (fixture_data / "narrative.json").write_text(json.dumps(metadata), encoding="utf-8")
                    result = subprocess.run(["bash", "-c", script], cwd=root, env=env, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    calls = [json.loads(line) for line in (root / "calls.jsonl").read_text().splitlines()]
                    create = next(row for row in calls if row["command"] == "gh" and row["args"][:2] == ["pr", "create"])
                    self.assertIn("--draft", create["args"])
                    self.assertEqual(create["args"][create["args"].index("--base") + 1], "develop")
                    self.assertIn("--body-file", create["args"])
                    self.assertIn("Karen's signed PASS", create["body"])
                    labels = [row for row in calls if row["command"] == "gh" and "--add-label" in row["args"]]
                    self.assertEqual(bool(labels), hold)
                    self.assertFalse(any(row["args"][:2] == ["pr", "merge"] for row in calls))
                    self.assertFalse((root / "PWNED").exists())
                    if mode == "full":
                        self.assertIn(metadata["headline"], create["body"])
                        self.assertIn(metadata["dropBody"], create["body"])
