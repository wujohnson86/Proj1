"""
Mail Agent (read-only)
======================

A command-line agent powered by Claude that can browse your own IMAP
mailbox (e.g. self-hosted Dovecot) using the read-only tools from
mail_mcp_server.py: list folders, list recent messages, and read a message.

This agent is intentionally READ-ONLY — it can't move, delete, or modify
anything in your mailbox. That makes it safe to point at a real account
while you get a feel for how it behaves. (A "move to Junk/Spam" tool can
be added later as a separate, more careful step.)

Setup:
    pip install anthropic mcp
    export ANTHROPIC_API_KEY="your-anthropic-key-here"
    export IMAP_HOST="mail.yourdomain.com"
    export IMAP_USER="you@yourdomain.com"
    export IMAP_PASSWORD="your-password"
    export IMAP_PORT="993"     # optional, defaults to 993

Run:
    python3 mail_agent.py

Try asking:
    "What folders do I have?"
    "Show me my 5 most recent emails"
    "What's in the latest message from <someone>?"
    "Summarize my unread-looking messages in the inbox"

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
    if not all(os.environ.get(v) for v in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")):
        print("Warning: IMAP_HOST / IMAP_USER / IMAP_PASSWORD are not all set — "
              "mailbox lookups will fail until you export them.\n")

    client = Anthropic()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "mail_mcp_server.py")],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            mcp_tools = (await session.list_tools()).tools
            tools = anthropic_tool_schema(mcp_tools)
            print(f"Connected to MCP server. Available tools: {[t['name'] for t in tools]}")
            print("Ask me about your mailbox (read-only) — type 'quit' to exit.\n")

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
