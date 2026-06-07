"""
Crypto Agent
============

A command-line agent powered by Claude (Anthropic API) that can answer
questions about cryptocurrency prices using the get_crypto_price tool from
crypto_mcp_server.py over MCP.

This is the same pattern as weather_agent.py — just pointed at a different
MCP server. No second API key needed this time (CoinGecko is free/keyless).

Setup:
    pip install anthropic mcp
    export ANTHROPIC_API_KEY="your-anthropic-key-here"

Run:
    python3 crypto_agent.py

Try asking:
    "What's the price of Bitcoin?"
    "Compare Ethereum and Dogecoin in EUR"

Type "quit" or "exit" to stop.
"""

import asyncio
import os
import sys

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-4-6"


def anthropic_tool_schema(mcp_tools):
    tools = []
    for tool in mcp_tools:
        tools.append({
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.inputSchema,
        })
    return tools


async def run_agent():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: please set ANTHROPIC_API_KEY (export ANTHROPIC_API_KEY=...)")

    client = Anthropic()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "crypto_mcp_server.py")],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            mcp_tools = (await session.list_tools()).tools
            tools = anthropic_tool_schema(mcp_tools)
            print(f"Connected to MCP server. Available tools: {[t['name'] for t in tools]}")
            print("Ask me about crypto prices (type 'quit' to exit).\n")

            messages = []
            while True:
                try:
                    user_input = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if user_input.lower() in ("quit", "exit"):
                    break
                if not user_input:
                    continue

                messages.append({"role": "user", "content": user_input})

                while True:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=1024,
                        tools=tools,
                        messages=messages,
                    )
                    messages.append({"role": "assistant", "content": response.content})

                    if response.stop_reason != "tool_use":
                        for block in response.content:
                            if block.type == "text":
                                print(f"\nClaude: {block.text}\n")
                        break

                    tool_results = []
                    for block in response.content:
                        if block.type != "tool_use":
                            continue
                        print(f"  [calling tool '{block.name}' with input {block.input}]")
                        result = await session.call_tool(block.name, block.input)
                        result_text = "".join(
                            part.text for part in result.content if part.type == "text"
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        })
                    messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    asyncio.run(run_agent())
