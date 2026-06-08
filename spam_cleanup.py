"""
Spam Cleanup (guided, MCP-powered)
==================================

A guided, step-by-step tool that helps you find and move likely-spam
messages to your mailbox's SPAM folder. It talks to your IMAP/Dovecot
mailbox through the same mail_mcp_server.py used by mail_agent.py — but
unlike a free-chat agent, THIS script controls the workflow itself and
asks for your explicit confirmation before moving anything.

Why not just let Claude decide what to move?
Moving mail is a real, hard-to-reverse action on your live mailbox. This
script keeps a human (you) in charge of every decision: it shows you
exactly what it found and only moves messages you explicitly say "yes" to.

The flow:
    1. List your IMAP folders
    2. You pick which folder to scan (default: INBOX)
    3. You give one or more subject keywords to search for
    4. It shows you every match (sender / subject / date)
    5. You confirm which ones (if any) should be moved
    6. It moves only the confirmed messages to your SPAM folder

Setup (same as mail_agent.py):
    pip install mcp
    export IMAP_HOST="ec2.snoopy.org"
    export IMAP_USER="johnson"
    export IMAP_PASSWORD="your-password"
    export IMAP_PORT="993"     # optional, defaults to 993

Run:
    python3 spam_cleanup.py

Note: this script does NOT use the Anthropic API — it talks to the MCP
mail server directly, since the workflow here is fully scripted rather
than something we want an LLM improvising on.
"""

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def call_text(result) -> str:
    """Pull the plain-text content out of an MCP tool result."""
    return "".join(part.text for part in result.content if part.type == "text")


def parse_message_lines(listing: str):
    """Turn a '[id 123] From: ... | Subject: ... | Date: ...' listing into
    a list of (id, line) tuples for easy display and lookup."""
    entries = []
    for line in listing.splitlines():
        line = line.strip()
        if not line.startswith("[id "):
            continue
        msg_id = line.split("]")[0].replace("[id ", "").strip()
        entries.append((msg_id, line))
    return entries


async def run():
    if not all(os.environ.get(v) for v in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")):
        sys.exit(
            "Error: please set IMAP_HOST, IMAP_USER, and IMAP_PASSWORD "
            "environment variables first (see the top of this file for an example)."
        )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "mail_mcp_server.py")],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            # --- Step 1: list folders ---
            print("Looking up your IMAP folders...\n")
            folders_text = call_text(await session.call_tool("list_mail_folders", {}))
            print(folders_text)
            print()

            # --- Step 2: pick a folder to scan ---
            folder = input("Which folder do you want to scan? [INBOX]: ").strip() or "INBOX"

            # --- Step 3: ask for keyword(s) ---
            raw_keywords = input(
                "Enter one or more spam subject keywords, separated by commas\n"
                "(e.g. \"viagra, you've won, free money\"): "
            ).strip()
            keywords = [k.strip() for k in raw_keywords.split(",") if k.strip()]
            if not keywords:
                print("No keywords entered — nothing to search for. Exiting.")
                return

            # --- Step 4: search and show matches ---
            all_matches = {}  # message_id -> display line
            for keyword in keywords:
                print(f"\nSearching '{folder}' for subjects containing '{keyword}'...")
                result_text = call_text(await session.call_tool(
                    "search_messages_by_subject",
                    {"keyword": keyword, "folder": folder, "count": 50},
                ))
                print(result_text)
                for msg_id, line in parse_message_lines(result_text):
                    all_matches[msg_id] = line

            if not all_matches:
                print("\nNo matching messages found. Nothing to do.")
                return

            print(f"\n{'=' * 60}")
            print(f"Found {len(all_matches)} unique matching message(s):\n")
            ids_in_order = list(all_matches.keys())
            for i, msg_id in enumerate(ids_in_order, start=1):
                print(f"  {i}. {all_matches[msg_id]}")

            # --- Step 5: confirm which ones to move ---
            print(
                "\nWhich ones should be moved to SPAM?\n"
                "  - Enter numbers separated by commas (e.g. 1,3,4)\n"
                "  - Enter 'all' to move all of them\n"
                "  - Enter nothing / 'none' to move nothing"
            )
            choice = input("Your choice: ").strip().lower()

            if choice in ("", "none", "no"):
                print("Okay — moving nothing. Exiting.")
                return
            elif choice == "all":
                chosen_ids = ids_in_order
            else:
                chosen_ids = []
                for token in choice.split(","):
                    token = token.strip()
                    if token.isdigit() and 1 <= int(token) <= len(ids_in_order):
                        chosen_ids.append(ids_in_order[int(token) - 1])
                if not chosen_ids:
                    print("Didn't recognize that input — moving nothing. Exiting.")
                    return

            print(f"\nAbout to move {len(chosen_ids)} message(s) from '{folder}' to 'SPAM':")
            for msg_id in chosen_ids:
                print(f"  - {all_matches[msg_id]}")
            final_confirm = input("\nType 'yes' to confirm and move these now: ").strip().lower()
            if final_confirm != "yes":
                print("Not confirmed — moving nothing. Exiting.")
                return

            # --- Step 6: move the confirmed messages ---
            print()
            for msg_id in chosen_ids:
                outcome = call_text(await session.call_tool(
                    "move_message_to_folder",
                    {"message_id": msg_id, "destination_folder": "SPAM", "source_folder": folder},
                ))
                print(outcome)

            print("\nDone.")


if __name__ == "__main__":
    asyncio.run(run())
