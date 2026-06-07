"""
Weather Agent
=============

A small command-line agent powered by Claude (Anthropic API) that can
answer questions about the weather by calling the get_weather tool from
weather_mcp_server.py over MCP (Model Context Protocol).

Setup (run these once in your terminal):
    pip install anthropic mcp

Set your two API keys as environment variables (replace with your real keys):
    export ANTHROPIC_API_KEY="your-anthropic-key-here"
    export OPENWEATHER_API_KEY="your-openweather-key-here"

Then run the agent:
    python3 weather_agent.py

You'll get a simple prompt where you can type things like:
    "What's the weather like in Tokyo?"
    "Compare the weather in Paris and Berlin"

Type "quit" or "exit" to stop.

How it works (high level):
1. This script starts weather_mcp_server.py as a subprocess and connects
   to it using MCP.
2. It asks that server what tools it has (just "get_weather").
3. It sends your question to Claude, telling Claude about that tool.
4. If Claude decides it needs the weather, it asks this script to call the
   tool; this script calls the MCP server, gets the result, and gives it
   back to Claude so it can write you a final answer.
"""

import asyncio
import os
import sys

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-4-6"


def anthropic_tool_schema(mcp_tools):
    """Convert MCP tool descriptions into the format Claude's API expects."""
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
    if not os.environ.get("OPENWEATHER_API_KEY"):
        print("Warning: OPENWEATHER_API_KEY is not set — weather lookups will fail "
              "until you export it.\n")

    client = Anthropic()

    # This launches weather_mcp_server.py as a subprocess and talks to it
    # over stdin/stdout — that's the "stdio" transport in MCP.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "weather_mcp_server.py")],
        # By default the MCP library only passes a safe subset of environment
        # variables to the subprocess. Pass our own env through explicitly so
        # the server can see OPENWEATHER_API_KEY.
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            mcp_tools = (await session.list_tools()).tools
            tools = anthropic_tool_schema(mcp_tools)
            print(f"Connected to MCP server. Available tools: {[t['name'] for t in tools]}")
            print("Ask me about the weather (type 'quit' to exit).\n")

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

                # Keep going until Claude gives a final text answer
                # (it may need to call the tool one or more times first).
                while True:
                    response = client.messages.create(
                        model=MODEL,
                        max_tokens=1024,
                        tools=tools,
                        messages=messages,
                    )
                    messages.append({"role": "assistant", "content": response.content})

                    if response.stop_reason != "tool_use":
                        # Claude is done — print its final reply.
                        for block in response.content:
                            if block.type == "text":
                                print(f"\nClaude: {block.text}\n")
                        break

                    # Claude wants to call one or more tools — do that via MCP
                    # and send the results back.
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
