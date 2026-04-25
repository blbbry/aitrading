import os
import json
import anthropic
from dotenv import load_dotenv

# Always load from the project root regardless of working directory
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(_ENV_PATH)

MODEL = "claude-sonnet-4-6"


class BaseBot:
    name: str = "BaseBot"
    role: str = ""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _run(self, system: str, messages: list, tools: list = None, max_tokens: int = 2048) -> str:
        kwargs = dict(model=MODEL, max_tokens=max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        response = self.client.messages.create(**kwargs)

        while response.stop_reason == "tool_use":
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            tool_results = []
            for tu in tool_uses:
                result = self._handle_tool(tu.name, tu.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result) if not isinstance(result, str) else result,
                })
            messages = messages + [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": tool_results},
            ]
            kwargs["messages"] = messages
            response = self.client.messages.create(**kwargs)

        return next((b.text for b in response.content if hasattr(b, "text")), "")

    def _handle_tool(self, name: str, inputs: dict) -> any:
        raise NotImplementedError(f"{self.name} has no tool handler for {name}")

    def analyze(self, symbol: str, context: dict = None) -> dict:
        raise NotImplementedError
