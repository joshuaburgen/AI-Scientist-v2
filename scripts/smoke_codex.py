"""Run a small real subscription call: python -m scripts.smoke_codex."""
import json
import argparse
import os
import tempfile
from pathlib import Path
from ai_scientist.codex_backend import CodexClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--full", action="store_true", help="Also test text/history, batching and vision through pipeline entry points")
    args = parser.parse_args()
    function = {"name": "check", "description": "Return ok=true and message=connected.", "parameters": {
        "type": "object", "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
        "required": ["ok", "message"], "additionalProperties": False}}
    response = CodexClient().create(
        messages=[{"role": "user", "content": "Connectivity test. Return ok=true and message=connected."}],
        tools=[{"type": "function", "function": function}],
        tool_choice={"type": "function", "function": {"name": "check"}},
    )
    result = json.loads(response.choices[0].message.tool_calls[0].function.arguments)
    assert result == {"ok": True, "message": "connected"}, result
    print(json.dumps(result))
    print("Subscription backend OK; no API key used.")
    if args.full:
        os.environ["AI_SCIENTIST_BACKEND"] = "codex"
        from ai_scientist import llm, vlm
        from ai_scientist.treesearch.backend import query
        from ai_scientist.treesearch.backend.utils import FunctionSpec
        from PIL import Image

        spec = FunctionSpec("check", function["parameters"], function["description"])
        assert query("Return the requested JSON.", "Return ok=true and message=connected.",
                     model="anthropic.claude-3-5-sonnet", func_spec=spec) == result
        client, model = llm.create_client("gpt-4o")
        text, _ = llm.get_response_from_llm(
            "What number did I give? Reply only with the number.", client, model, "Follow the user request.",
            msg_history=[{"role": "user", "content": "My number is 17."},
                         {"role": "assistant", "content": "Understood."}],
        )
        assert text.strip() == "17", text
        replies, _ = llm.get_batch_responses_from_llm("Reply only OK.", client, model,
                                                     "Follow the request.", n_responses=2)
        assert len(replies) == 2 and all(reply.strip() == "OK" for reply in replies), replies
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "green.png"
            Image.new("RGB", (64, 64), (0, 200, 0)).save(image)
            client, model = vlm.create_client("gpt-4o-2024-11-20")
            answer, _ = vlm.get_response_from_vlm("Name the dominant color. Reply with one English word.",
                                                 str(image), client, model, "Inspect the image.")
            assert "green" in answer.lower(), answer
        print("Pipeline structured output, history, batch and vision OK.")


if __name__ == "__main__":
    main()
