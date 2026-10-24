"""Interactive chatbot that sanitizes each message before calling the LLM."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from groq import GroqError
from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware
from langchain_groq import ChatGroq
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import (
    MCPToolCallRequest,
    MCPToolCallResult,
)


async def logging_interceptor(
    request: MCPToolCallRequest,
    handler: Callable[
        [MCPToolCallRequest],
        Awaitable[MCPToolCallResult],
    ],
) -> MCPToolCallResult:
    """Log tool activity without writing raw, potentially sensitive arguments."""
    argument_names = ", ".join(sorted(request.args))
    print(
        f"\n[MCP] Calling {request.name} "
        f"(arguments: {argument_names or 'none'})"
    )
    result = await handler(request)
    if getattr(result, "isError", False) or getattr(result, "status", None) == "error":
        print(f"[MCP] {request.name} failed; result content withheld.")
    else:
        print(f"[MCP] {request.name} completed; result content withheld.")
    return result


def _tool_result_text(result: Any) -> str:
    """Extract text from standard adapter return values; fail closed otherwise."""
    if getattr(result, "status", None) == "error":
        raise RuntimeError("MCP sanitizer reported an error; refusing to continue.")
    if getattr(result, "isError", False):
        raise RuntimeError("MCP sanitizer reported an error; refusing to continue.")
    if isinstance(result, tuple) and result:
        result = result[0]
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, str):
            return content
    content_items = getattr(result, "content", None)
    if isinstance(result, list):
        content_items = result
    if isinstance(content_items, list):
        text_parts = [
            item["text"]
            if isinstance(item, dict)
            else item.text
            for item in content_items
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            )
            or isinstance(getattr(item, "text", None), str)
        ]
        if text_parts:
            return "\n".join(text_parts)
    raise TypeError(
        f"Sanitizer returned an unsupported result type: {type(result).__name__}"
    )


async def _sanitize_message(
    text: str,
    tools_by_name: dict[str, Any],
) -> str:
    """Run both required scrubbers in order; never fall back to unsanitized text."""
    secrets_tool = tools_by_name.get("mask_api_keys_and_secrets")
    pii_tool = tools_by_name.get("mask_pii_data")
    if secrets_tool is None or pii_tool is None:
        missing = [
            name
            for name, tool in (
                ("mask_api_keys_and_secrets", secrets_tool),
                ("mask_pii_data", pii_tool),
            )
            if tool is None
        ]
        raise RuntimeError(f"Required MCP sanitizer tool(s) missing: {missing}")

    secret_scrubbed = _tool_result_text(
        await secrets_tool.ainvoke({"text": text})
    )
    return _tool_result_text(
        await pii_tool.ainvoke({"text": secret_scrubbed})
    )


def _load_groq_api_key() -> None:
    """Load the Groq key from the project env file and fail with setup guidance."""
    project_dir = Path(__file__).resolve().parent
    dotenv_path = project_dir / ".env"
    if not dotenv_path.is_file():
        dotenv_path = project_dir / ".venv" / ".env"

    load_dotenv(dotenv_path=dotenv_path, override=False)
    if not os.getenv("GROQ_API_KEY"):
        raise RuntimeError(
            "GROQ_API_KEY is not set. Add GROQ_API_KEY=... to a .env file "
            "in the project directory, or set it in your environment."
        )


async def chat() -> None:
    _load_groq_api_key()
    model_name = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    client = MultiServerMCPClient(
        {
            "security": {
                "transport": "sse",
                "url": "http://127.0.0.1:8000/sse",
            }
        },
        tool_interceptors=[logging_interceptor],
    )
    tools = await client.get_tools()
    tools_by_name = {tool.name: tool for tool in tools}

    system_message = (
        "You are a privacy-conscious assistant. The trusted client has already "
        "invoked mask_api_keys_and_secrets and mask_pii_data on every user "
        "message before sending it here. Never request, reconstruct, infer, or "
        "repeat redacted values. Treat any unredacted secret or personal data "
        "as sensitive; invoke both sanitizer tools before using or responding "
        "to any message that contains raw user-provided text. Do not claim that "
        "regex scanning detects every possible secret or personal identifier."
    )
    model = ChatGroq(model=model_name, temperature=0)
    agent = create_agent(
        model,
        tools,
        system_prompt=system_message,
        middleware=[
            SummarizationMiddleware(
                model,
                trigger=("messages", 3),
                keep=("messages", 2),
            )
        ],
    )

    print("Secure assistant ready. Type 'exit' or 'quit' to stop.")
    conversation_history = []
    while True:
        try:
            user_text = await asyncio.to_thread(input, "\nYou: ")
        except EOFError:
            print("\nGoodbye.")
            break

        if user_text.strip().lower() in {"exit", "quit"}:
            print("Goodbye.")
            break
        if not user_text.strip():
            continue

        sanitized_text = await _sanitize_message(user_text, tools_by_name)
        try:
            response = await agent.ainvoke(
                {
                    "messages": [
                        *conversation_history,
                        {"role": "user", "content": sanitized_text},
                    ]
                }
            )
        except GroqError as exc:
            print(
                f"\nGroq request failed ({type(exc).__name__}) for model "
                f"'{model_name}'. Check that GROQ_MODEL names a model enabled "
                "for your Groq account and that the API key is valid."
            )
            continue
        conversation_history = response["messages"]
        final_message = response["messages"][-1]
        print(f"\nAssistant: {final_message.content}")


def main() -> None:
    """Start the asynchronous interactive client."""
    asyncio.run(chat())


if __name__ == "__main__":
    main()
