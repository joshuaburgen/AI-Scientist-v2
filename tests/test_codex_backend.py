import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_scientist.codex_backend import CodexClient, strict_schema, run_cli


class CodexBackendTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"AI_SCIENTIST_BACKEND": "codex"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.cli = patch("ai_scientist.codex_backend.cli_command", return_value=["codex"])
        self.cli.start()
        self.addCleanup(self.cli.stop)

    def fake_run(self, answer="answer", inspect=None):
        def run(command, prompt, directory, environment, timeout):
            if inspect:
                inspect(command, prompt, directory, environment, timeout)
            Path(command[command.index("--output-last-message") + 1]).write_text(answer, encoding="utf-8")
            return 0, json.dumps({"type": "turn.completed", "usage": {
                "input_tokens": 12, "output_tokens": 3, "cached_input_tokens": 2}}), ""
        return patch("ai_scientist.codex_backend.run_cli", side_effect=run)

    def test_history_unicode_stdin_auth_and_isolation(self):
        def inspect(command, prompt, directory, environment, timeout):
            self.assertIn("你好", prompt)
            self.assertIn('"role": "assistant"', prompt)
            self.assertEqual(command[-1], "-")
            self.assertNotIn("你好", command)
            self.assertNotIn("OPENAI_API_KEY", environment)
            self.assertNotIn("CODEX_API_KEY", environment)
            self.assertNotIn("OPENAI_BASE_URL", environment)
            self.assertIn('forced_login_method="chatgpt"', command)
            self.assertIn("--ignore-user-config", command)
            self.assertEqual(command[command.index("--sandbox") + 1], "read-only")
            self.assertNotEqual(Path(directory), Path.cwd())
        with patch.dict(os.environ, {"OPENAI_API_KEY": "unused", "CODEX_API_KEY": "unused", "OPENAI_BASE_URL": "unused"}), self.fake_run(inspect=inspect):
            response = CodexClient().create(messages=[{"role": "assistant", "content": "你好"}])
        self.assertEqual(response.choices[0].message.content, "answer")
        self.assertEqual(response.usage.prompt_tokens, 12)

    def test_structured_result_and_optional_fields(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}, "note": {"type": "string"}}, "required": ["ok"]}
        function = {"name": "evaluate", "description": "Evaluate run", "parameters": schema}
        def inspect(command, *args):
            sent = json.loads(Path(command[command.index("--output-schema") + 1]).read_text())
            self.assertEqual(sent["required"], ["ok", "note"])
            self.assertFalse(sent["additionalProperties"])
        with self.fake_run('{"ok":true,"note":null}', inspect):
            response = CodexClient().create(messages=[], tools=[{"type": "function", "function": function}], tool_choice={"type": "function", "function": {"name": "evaluate"}})
        call = response.choices[0].message.tool_calls[0].function
        self.assertEqual(call.name, "evaluate")
        self.assertEqual(json.loads(call.arguments), {"ok": True})
        self.assertNotIn("additionalProperties", schema)

    def test_image_payload_and_cleanup(self):
        paths = []
        def inspect(command, prompt, *args):
            path = Path(command[command.index("--image") + 1])
            paths.append(path)
            self.assertEqual(path.read_bytes(), b"image bytes")
            self.assertIn("Attached image 1", prompt)
            self.assertNotIn("base64,", prompt)
        with self.fake_run(inspect=inspect):
            CodexClient().create(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"image bytes").decode()}}]}])
        self.assertFalse(paths[0].exists())

    def test_remote_images_rejected(self):
        with self.assertRaisesRegex(ValueError, "base64"):
            CodexClient().create(messages=[{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://example.com/image.png"}}]}])

    def test_batch_uses_independent_turns_and_sums_usage(self):
        from ai_scientist.utils.token_tracker import token_tracker
        token_tracker.reset()
        with self.fake_run() as run:
            response = CodexClient().create(messages=[], n=3)
        self.assertEqual(run.call_count, 3)
        self.assertEqual(len(response.choices), 3)
        self.assertEqual(response.usage.prompt_tokens, 36)
        self.assertEqual(token_tracker.get_summary()["codex"]["tokens"]["prompt"], 36)

    def test_invalid_json_or_wrong_type_rejected(self):
        import jsonschema
        function = {"name": "check", "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
        for answer, error in [("not json", json.JSONDecodeError), ('{"ok":"yes"}', jsonschema.ValidationError)]:
            with self.subTest(answer=answer), self.fake_run(answer), self.assertRaises(error):
                CodexClient().create(messages=[], tools=[{"type": "function", "function": function}],
                                     tool_choice={"type": "function", "function": {"name": "check"}})

    def test_real_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(subprocess.TimeoutExpired):
            run_cli([sys.executable, "-c", "import time; time.sleep(30)"], "", tmp, os.environ.copy(), 0.1)

    def test_failure_does_not_retry_or_fallback(self):
        with patch("ai_scientist.codex_backend.run_cli", return_value=(1, '', 'usage limit')) as run:
            with self.assertRaisesRegex(RuntimeError, "subscription limits"):
                CodexClient().create(messages=[])
        self.assertEqual(run.call_count, 1)

    def test_failed_turn_even_with_zero_exit(self):
        with patch("ai_scientist.codex_backend.run_cli", return_value=(0, '{"type":"turn.failed"}', '')):
            with self.assertRaises(RuntimeError):
                CodexClient().create(messages=[])

    def test_timeout(self):
        with patch("ai_scientist.codex_backend.run_cli", side_effect=subprocess.TimeoutExpired("codex", 600)):
            with self.assertRaisesRegex(RuntimeError, "no API fallback"):
                CodexClient().create(messages=[])

    def test_actual_subprocess_stdin(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, err = run_cli([sys.executable, "-c", "import sys; print(sys.stdin.read())"], "hello", tmp, os.environ.copy(), 10)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "hello")

    def test_pipeline_routes_including_hardcoded_models(self):
        from ai_scientist import llm, vlm
        from ai_scientist.treesearch import backend
        from ai_scientist.utils.token_tracker import token_tracker

        token_tracker.reset()
        self.assertIsInstance(backend.get_ai_client("anthropic.claude-3-5-sonnet"), CodexClient)
        with self.fake_run("test response"):
            client, model = llm.create_client("gpt-4o")
            text, history = llm.get_response_from_llm("question", client, model, "system")
            self.assertEqual(text, "test response")
            replies, histories = llm.get_batch_responses_from_llm("question", client, model, "system", n_responses=2)
            self.assertEqual(len(replies), 2)
            self.assertEqual(backend.query("system", "question", model="anthropic.claude-3-5-sonnet"), "test response")
            client, model = vlm.create_client("gpt-4o-2024-11-20")
            text, _ = vlm.get_response_from_vlm("question", [], client, model, "system")
            self.assertEqual(text, "test response")
        self.assertIsNone(token_tracker.get_summary()["codex"]["cost (USD)"])
        self.assertEqual(token_tracker.get_summary()["codex"]["tokens"]["prompt"], 60)
        self.assertEqual(len(token_tracker.get_interactions()["codex"]), 5)

    def test_tree_search_function_contract(self):
        from ai_scientist.treesearch.backend import query
        from ai_scientist.treesearch.backend.utils import FunctionSpec

        spec = FunctionSpec("result", {"type": "object", "properties": {"score": {"type": "number"}}, "required": ["score"]}, "Return score")
        with self.fake_run('{"score":0.8}'):
            self.assertEqual(query("evaluate", "result", model="gpt-4o", func_spec=spec), {"score": 0.8})

    def test_api_routing_preserved(self):
        from ai_scientist import llm
        from ai_scientist.treesearch import backend

        with patch.dict(os.environ, {"AI_SCIENTIST_BACKEND": "api"}), patch("ai_scientist.llm.openai.OpenAI") as api:
            self.assertIs(llm.create_client("gpt-4o")[0], api.return_value)
            self.assertIs(backend.get_ai_client("gpt-4o"), api.return_value)


if __name__ == "__main__":
    unittest.main()
