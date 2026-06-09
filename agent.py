"""
All-in-One Agent
================

A single command-line agent powered by Claude that connects to THREE MCP
servers at once — weather, crypto, and news — and lets Claude figure out
on its own which tool(s) to use based on what you ask.

This is the same idea as weather_agent.py / crypto_agent.py / news_agent.py,
just generalized to manage multiple MCP server connections at the same time.
Claude sees all the tools from all three servers in one list, and picks
whichever one(s) fit your question — including using more than one tool to
answer a single question (e.g. "what's the weather in Tokyo and the price
of Bitcoin?").

Setup:
    pip install anthropic mcp
    export ANTHROPIC_API_KEY="your-anthropic-key-here"
    export OPENWEATHER_API_KEY="your-openweather-key-here"
    export NEWSAPI_KEY="your-newsapi-key-here"

Run:
    python3 agent.py

Try asking:
    "What's the weather in Tokyo?"
    "What's the price of Bitcoin?"
    "What's the top tech news today?"
    "Give me the weather in Paris AND the price of Ethereum"

Type "quit" or "exit" to stop.
"""

import asyncio
import os
import sys
from contextlib import AsyncExitStack

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-sonnet-4-6"

# Each entry here is (friendly name, server script filename).
# Add more (server_name, script) pairs to plug in more MCP servers later.
SERVERS = [
    ("weather", "weather_mcp_server.py"),
    ("crypto", "crypto_mcp_server.py"),
    ("news", "news_mcp_server.py"),
    ("shodan", "shodan_mcp_server.py"),
]


async def connect_to_server(stack: AsyncExitStack, script_name: str) -> ClientSession:
    """Launch one MCP server as a subprocess and open a session with it."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), script_name)],
        env=dict(os.environ),
    )
    read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
    session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
    await session.initialize()
    return session


async def run_agent():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: please set ANTHROPIC_API_KEY (export ANTHROPIC_API_KEY=...)")
    if not os.environ.get("SHODAN_API_KEY"):
        print("Warning: SHODAN_API_KEY not set — search_shodan will fail, "
              "but lookup_ip (InternetDB) will still work without a key.\n")

    client = Anthropic()

    # AsyncExitStack lets us open several MCP connections at once and have
    # them all cleanly closed together when the program exits.
    async with AsyncExitStack() as stack:
        # tool_name -> the session (server connection) that owns that tool
        tool_to_session = {}
        all_tools = []

        for server_name, script_name in SERVERS:
            try:
                session = await connect_to_server(stack, script_name)
            except Exception as e:
                print(f"Warning: couldn't start '{server_name}' server ({script_name}): {e}")
                continue

            mcp_tools = (await session.list_tools()).tools
            for tool in mcp_tools:
                tool_to_session[tool.name] = session
                all_tools.append({
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                })
            print(f"Connected to '{server_name}' server. Tools: {[t.name for t in mcp_tools]}")

        if not all_tools:
            sys.exit("No MCP servers connected successfully — nothing to do.")

        print("\nAsk me about weather, crypto prices, or news (type 'quit' to exit).")
        print("I can also combine tools — e.g. 'weather in Tokyo and price of Bitcoin'.\n")

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
                    tools=all_tools,
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
                    session = tool_to_session[block.name]
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
