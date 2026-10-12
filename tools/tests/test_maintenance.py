"""Regression coverage for article routing, transport failures, and no-op slates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from tools.chiefs_narrative import collect, generate, providers


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
        self.assertIn('"total_tokens": 30', log.call_args.args[0])


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
