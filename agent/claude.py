import os
import anthropic
from agent.prompts import SYSTEM_PROMPT
from agent.tools import TOOLS
from agent.tool_handlers import handle_tool

MAX_ITERATIONS = 10
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        _client = anthropic.Anthropic(api_key=key)
    return _client


def run_agent(history: list[dict], conversation_id: int) -> str:
    messages = [{"role": m["role"], "content": m["content"]} for m in history]
    system = SYSTEM_PROMPT.replace("{conversation_id}", str(conversation_id))
    model = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

    for _ in range(MAX_ITERATIONS):
        response = _get_client().messages.create(
            model=model,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages
        )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = handle_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result
                    })
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return "Disculpe, hubo un inconveniente. Un momento, le comunicamos con un agente."
