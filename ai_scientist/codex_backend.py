"""ChatGPT subscription backend using the official, authenticated Codex CLI.

This deliberately does not read auth.json or implement private HTTP endpoints.
The small completion-shaped adapter keeps the existing research pipeline intact.
"""

import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace as NS

import jsonschema


def use_codex(model=""):
    return model == "codex" or os.environ.get("AI_SCIENTIST_BACKEND") == "codex"


def configure_backend(backend=None):
    if backend is not None:
        os.environ["AI_SCIENTIST_BACKEND"] = backend


def cli_command():
    """Avoid PowerShell execution policies and cmd.exe argument interpretation."""
    executable = os.environ.get("AI_SCIENTIST_CODEX_BIN") or shutil.which("codex")
    if not executable:
        raise RuntimeError("Install the official Codex CLI and run codex login first.")
    path = Path(executable)
    if os.name == "nt" and path.suffix.lower() in {".cmd", ".ps1", ".bat"}:
        script = path.parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if not node or not script.is_file():
            raise RuntimeError("Set AI_SCIENTIST_CODEX_BIN to the native codex.exe path.")
        return [node, str(script)]
    return [str(path)]


def strict_schema(schema):
    """Convert optional object properties to nullable required properties."""
    schema = copy.deepcopy(schema)

    def visit(value):
        if isinstance(value, list):
            return [visit(item) for item in value]
        if not isinstance(value, dict):
            return value
        value = {key: visit(item) for key, item in value.items()}
        if value.get("type") == "object" or "properties" in value:
            properties = value.setdefault("properties", {})
            required = value.get("required", [])
            for key in properties:
                if key not in required:
                    properties[key] = {"anyOf": [properties[key], {"type": "null"}]}
            value["required"] = list(properties)
            value["additionalProperties"] = False
        return value

    return visit(schema)


def restore_optional(value, schema):
    """Remove nullable placeholders before validating against the original schema."""
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        return {
            key: restore_optional(item, properties.get(key, {}))
            for key, item in value.items()
            if item is not None or key in required or key not in properties
        }
    if isinstance(value, list):
        return [restore_optional(item, schema.get("items", {})) for item in value]
    return value


def prepare_messages(messages, directory):
    transcript, images = [], []
    for message in messages:
        content = message.get("content") or ""
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append(part["text"])
                elif part.get("type") == "image_url":
                    url = part["image_url"]["url"]
                    if not url.startswith("data:image/") or ";base64," not in url:
                        raise ValueError("Codex image input requires a base64 image data URL.")
                    header, payload = url.split(",", 1)
                    extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}.get(
                        header[5:].split(";")[0]
                    )
                    if extension is None:
                        raise ValueError("Unsupported Codex image format.")
                    image = directory / f"image-{len(images)}{extension}"
                    image.write_bytes(base64.b64decode(payload, validate=True))
                    images.append(str(image))
                    parts.append(f"[Attached image {len(images)}]")
                else:
                    raise ValueError(f"Unsupported message content: {part.get('type')}")
            content = "\n".join(parts)
        transcript.append({"role": message["role"], "content": content})
    prompt = (
        "You are the model backend for AI Scientist. Respond to the supplied conversation. "
        "Return only the requested answer, preserving requested code fences and formatting. "
        "Do not run commands, edit files, browse, or use tools. Experiments are executed by "
        "the caller. The JSON below is the conversation, with system messages describing "
        "the research task. Attached images are numbered in conversation order.\n"
        + json.dumps(transcript, ensure_ascii=False)
    )
    return prompt, images


def run_cli(command, prompt, directory, environment, timeout):
    process = subprocess.Popen(
        command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=directory, env=environment, text=True, encoding="utf-8", errors="replace",
    )
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        # The npm launcher may have a native child. Kill only this invocation's tree.
        import psutil

        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        process.kill()
        process.communicate()
        raise
    return process.returncode, stdout, stderr


class CodexClient:
    def __init__(self):
        self.chat = NS(completions=self)

    def create(self, *, messages, model="codex", n=1, tools=None, tool_choice=None, **kwargs):
        """Temperature/token-cap API options have no equivalent and are not forwarded.

        Each sample is an independent Codex turn; no automatic paid API fallback.
        """
        if n < 1:
            raise ValueError("n must be positive")
        function = None
        if tools:
            if len(tools) != 1 or tools[0].get("type") != "function":
                raise ValueError("Codex adapter supports one structured-output function.")
            function = tools[0]["function"]
            if tool_choice != {"type": "function", "function": {"name": function["name"]}}:
                raise ValueError("Codex adapter requires an explicit function choice.")
        choices, counts = [], {"input_tokens": 0, "output_tokens": 0, "cached_input_tokens": 0}
        for _ in range(n):
            with tempfile.TemporaryDirectory(prefix="ai-scientist-codex-") as tmp:
                directory = Path(tmp)
                prompt, images = prepare_messages(messages, directory)
                output = directory / "answer.txt"
                command = cli_command() + [
                    "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                    "--sandbox", "read-only", "--json", "--color", "never",
                    "-c", 'forced_login_method="chatgpt"',
                    "-c", "features.shell_tool=false",
                    "-c", "features.apply_patch_freeform=false",
                    "--output-last-message", str(output),
                ]
                selected_model = os.environ.get("AI_SCIENTIST_CODEX_MODEL")
                if selected_model:
                    command += ["--model", selected_model]
                if function:
                    schema_path = directory / "schema.json"
                    schema_path.write_text(json.dumps(strict_schema(function["parameters"])), encoding="utf-8")
                    command += ["--output-schema", str(schema_path)]
                    prompt += "\nReturn JSON for: " + function.get("description", function["name"])
                for image in images:
                    command += ["--image", image]
                command += ["-"]
                environment = os.environ.copy()
                for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
                    environment.pop(key, None)
                timeout = float(os.environ.get("AI_SCIENTIST_CODEX_TIMEOUT", "600"))
                try:
                    code, stdout, stderr = run_cli(command, prompt, tmp, environment, timeout)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f"Codex exceeded {timeout:g}s; no API fallback was used.") from exc
                events = []
                for line in stdout.splitlines():
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                failures = [event for event in events if event.get("type") in {"error", "turn.failed"}]
                if code or failures or not output.exists():
                    detail = json.dumps(failures, ensure_ascii=False) if failures else stderr[-2000:]
                    raise RuntimeError(
                        "Codex call failed. Check codex login status and subscription limits. " + detail
                    )
                content = output.read_text(encoding="utf-8").strip()
                if not content:
                    raise RuntimeError("Codex returned an empty answer.")
                for event in events:
                    if event.get("type") == "turn.completed":
                        from ai_scientist.utils.token_tracker import token_tracker

                        usage = event.get("usage", {})
                        token_tracker.add_tokens("codex", usage.get("input_tokens", 0) or 0,
                                                 usage.get("output_tokens", 0) or 0, 0,
                                                 usage.get("cached_input_tokens", 0) or 0)
                        token_tracker.add_interaction("codex", "", prompt, content, int(time.time()))
                        for key in counts:
                            counts[key] += usage.get(key, 0) or 0
                tool_calls = None
                if function:
                    parsed = restore_optional(json.loads(content), function["parameters"])
                    jsonschema.validate(parsed, function["parameters"])
                    tool_calls = [NS(function=NS(name=function["name"], arguments=json.dumps(parsed)))]
                choices.append(NS(message=NS(content=content, tool_calls=tool_calls)))
        return NS(
            choices=choices, model="codex", created=int(time.time()), system_fingerprint=None,
            usage=NS(prompt_tokens=counts["input_tokens"], completion_tokens=counts["output_tokens"],
                     prompt_tokens_details=NS(cached_tokens=counts["cached_input_tokens"]),
                     completion_tokens_details=NS(reasoning_tokens=0)),
        )
