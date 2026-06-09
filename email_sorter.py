"""
Email Sorter (training + auto mode)
====================================

A guided email sorting tool powered by Claude that reads your INBOX,
classifies each message into a target folder, and moves it — but ONLY
after you confirm (in training mode) or after you have trained it enough
to trust it (auto mode, opt-in with --auto flag).

IMPORTANT DESIGN RULES:
  - Messages are MOVED (copy + remove from source), never deleted.
  - "Remove from source" is the standard IMAP move — the message still
    exists in the destination folder. Nothing is permanently destroyed.
  - If in doubt, the sorter puts the message back in INBOX (the default
    folder in sorter_rules.json is "INBOX" = leave it alone).

Target folders (created automatically on first run if they don't exist):
  Promotions   — marketing, deals, sales, coupons
  Receipts     — order confirmations, invoices, shipping notices
  Newsletters  — blogs, digests, educational/informational feeds
  Consulting   — job opportunities, RFPs, freelance inquiries
  Finance      — bank alerts, bills, account statements
  Social       — LinkedIn, Twitter/X, Reddit, Facebook notifications
  Travel       — flight/hotel bookings, itineraries, car rentals
  Friends      — personal email from real humans you know

Messages that don't clearly fit stay in INBOX.

Usage:
    python3 email_sorter.py            # training mode (confirm each move)
    python3 email_sorter.py --auto     # auto mode (move without confirming)
    python3 email_sorter.py --count 50 # how many recent messages to process

Setup:
    export ANTHROPIC_API_KEY="your-anthropic-key-here"
    export IMAP_HOST="ec2.snoopy.org"
    export IMAP_USER="johnson"
    export IMAP_PASSWORD="your-password"

Learning:
    Every confirmed (or corrected) classification is saved to
    sorter_rules.json. On future runs, known sender domains are classified
    instantly without calling the Claude API, making it faster and cheaper
    the more you use it.
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = "claude-haiku-4-5-20251001"   # fast + cheap for classification

RULES_FILE = Path(__file__).parent / "sorter_rules.json"

# Folders we want to exist, with descriptions Claude uses to classify.
TARGET_FOLDERS = {
    "Promotions":  "Marketing emails, deals, sales, coupons, discount codes, store newsletters",
    "Receipts":    "Order confirmations, purchase receipts, invoices, shipping/delivery notices, subscription renewals",
    "Newsletters": "Informational digests, blog updates, educational content, mailing lists, industry news",
    "Consulting":  "Job opportunities, freelance inquiries, RFPs, recruiter emails, contract work",
    "Finance":     "Bank statements, credit card alerts, bill reminders, account notifications, tax documents",
    "Social":      "Notifications from LinkedIn, Twitter/X, Facebook, Reddit, GitHub, or other social platforms",
    "Travel":      "Flight confirmations, hotel bookings, car rentals, itineraries, travel updates",
    "Friends":     "Personal emails from real humans — friends, family, colleagues writing personally",
    "INBOX":       "Keep here — important, unclear, or doesn't fit any category above",
}

CLASSIFY_SYSTEM_PROMPT = """You are an email classifier. Given the From address, Subject line, and
optionally a short body snippet, classify the email into exactly one of these folders:

{folder_list}

Respond with ONLY a JSON object like:
{{"folder": "Receipts", "reason": "Order confirmation from Amazon"}}

Be decisive. If unsure between two folders, pick the more specific one.
Never suggest deleting anything. If truly ambiguous, use "INBOX"."""


def call_text(result) -> str:
    return "".join(part.text for part in result.content if part.type == "text")


def load_rules() -> dict:
    if RULES_FILE.exists():
        with open(RULES_FILE) as f:
            return json.load(f)
    return {"sender_rules": {}, "created_folders": []}


def save_rules(rules: dict):
    with open(RULES_FILE, "w") as f:
        json.dump(rules, f, indent=2)


def extract_domain(sender: str) -> str:
    """Pull the domain out of a From address for rule matching.
    e.g. '"Harbor Freight" <HarborFreight@e.harborfreight.com>' → 'harborfreight.com'
    Falls back to the full sender string if no email address found."""
    m = re.search(r"@([\w.\-]+)", sender)
    if m:
        domain = m.group(1).lower()
        # Strip leading subdomains to match at root domain level:
        # e.g. "e.harborfreight.com" → "harborfreight.com"
        parts = domain.split(".")
        return ".".join(parts[-2:]) if len(parts) > 2 else domain
    return sender.lower()[:60]


def parse_listing(listing: str) -> list[dict]:
    """Parse list_recent_messages output into structured dicts."""
    results = []
    for line in listing.splitlines():
        line = line.strip()
        if not line.startswith("[id "):
            continue
        msg_id = line.split("]")[0].replace("[id ", "").strip()
        from_m = re.search(r"From:\s*(.*?)\s*\|", line)
        subj_m = re.search(r"Subject:\s*(.*?)\s*\|", line)
        date_m = re.search(r"Date:\s*(.*?)$", line)
        results.append({
            "id": msg_id,
            "from": from_m.group(1).strip() if from_m else "",
            "subject": subj_m.group(1).strip() if subj_m else "",
            "date": date_m.group(1).strip() if date_m else "",
            "raw": line,
        })
    return results


async def ensure_folders(session, rules: dict, auto: bool) -> dict:
    """Create any target folders that don't exist yet. Returns updated rules."""
    existing_text = call_text(await session.call_tool("list_mail_folders", {}))
    existing = [f.strip() for f in existing_text.replace("Folders:", "").split(",")]

    to_create = [f for f in TARGET_FOLDERS if f != "INBOX" and f not in existing]
    if not to_create:
        return rules

    print(f"\nThese target folders don't exist yet: {', '.join(to_create)}")
    if not auto:
        confirm = input("Create them all now? [y/N]: ").strip().lower()
        if confirm != "y":
            print("Okay — skipping folder creation. Messages for those categories will stay in INBOX.")
            return rules

    for folder in to_create:
        result = call_text(await session.call_tool("create_mail_folder", {"folder_name": folder}))
        print(f"  {result}")
        if folder not in rules["created_folders"]:
            rules["created_folders"].append(folder)

    save_rules(rules)
    return rules


def classify_with_claude(client: Anthropic, sender: str, subject: str, snippet: str = "") -> tuple[str, str]:
    """Ask Claude to classify one email. Returns (folder, reason)."""
    folder_list = "\n".join(
        f"  {name}: {desc}" for name, desc in TARGET_FOLDERS.items()
    )
    prompt = CLASSIFY_SYSTEM_PROMPT.format(folder_list=folder_list)

    content = f"From: {sender}\nSubject: {subject}"
    if snippet:
        content += f"\nBody snippet: {snippet[:300]}"

    response = client.messages.create(
        model=MODEL,
        max_tokens=128,
        system=prompt,
        messages=[{"role": "user", "content": content}],
    )
    text = response.content[0].text.strip()

    try:
        data = json.loads(text)
        folder = data.get("folder", "INBOX")
        reason = data.get("reason", "")
        if folder not in TARGET_FOLDERS:
            folder = "INBOX"
        return folder, reason
    except Exception:
        return "INBOX", "Could not parse classification"


async def run(count: int, auto: bool):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Error: please set ANTHROPIC_API_KEY")
    if not all(os.environ.get(v) for v in ("IMAP_HOST", "IMAP_USER", "IMAP_PASSWORD")):
        sys.exit("Error: please set IMAP_HOST, IMAP_USER, IMAP_PASSWORD")

    rules = load_rules()
    client = Anthropic()

    server_params = StdioServerParameters(
        command=sys.executable,
        args=[os.path.join(os.path.dirname(__file__), "mail_mcp_server.py")],
        env=dict(os.environ),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Ensure target folders exist
            rules = await ensure_folders(session, rules, auto)

            # Fetch recent INBOX messages
            print(f"\nFetching {count} most recent INBOX messages...")
            listing = call_text(await session.call_tool(
                "list_recent_messages", {"folder": "INBOX", "count": count}
            ))
            messages = parse_listing(listing)
            if not messages:
                print("No messages found in INBOX.")
                return

            print(f"Found {len(messages)} messages. Processing...\n")
            print("=" * 70)

            moved = 0
            stayed = 0
            skipped = 0
            api_calls = 0

            for i, msg in enumerate(messages, 1):
                sender = msg["from"]
                subject = msg["subject"]
                domain = extract_domain(sender)

                # Fast path: known sender domain
                if domain in rules["sender_rules"]:
                    folder = rules["sender_rules"][domain]
                    reason = f"known sender ({domain})"
                    api_used = False
                else:
                    # Slow path: ask Claude — get body snippet first
                    peek = call_text(await session.call_tool(
                        "peek_message", {"message_id": msg["id"], "folder": "INBOX", "chars": 400}
                    ))
                    snippet_m = re.search(r"Snippet:\s*(.*)", peek, re.DOTALL)
                    snippet = snippet_m.group(1).strip() if snippet_m else ""
                    folder, reason = classify_with_claude(client, sender, subject, snippet)
                    api_calls += 1
                    api_used = True

                # In training mode, show proposal and ask for confirmation
                print(f"\n[{i}/{len(messages)}] {sender[:60]}")
                print(f"  Subject : {subject[:70]}")
                print(f"  Proposed: {folder}  ({reason})")

                if folder == "INBOX":
                    print("  → Already in INBOX, no move needed.")
                    stayed += 1
                    # Still let user teach the rule
                    if not auto and api_used:
                        teach = input("  Record this sender's domain for future runs? [y/N]: ").strip().lower()
                        if teach == "y":
                            rules["sender_rules"][domain] = "INBOX"
                            save_rules(rules)
                    continue

                if auto:
                    do_move = True
                    correction = folder
                else:
                    raw = input(
                        f"  Move to '{folder}'? [y / folder-name to correct / s to skip]: "
                    ).strip()
                    if raw.lower() == "y" or raw == "":
                        do_move = True
                        correction = folder
                    elif raw.lower() == "s":
                        do_move = False
                        correction = None
                        skipped += 1
                    else:
                        # User typed a different folder name
                        correction = raw.strip()
                        if correction not in TARGET_FOLDERS and correction != "INBOX":
                            print(f"  Unknown folder '{correction}' — skipping.")
                            do_move = False
                            correction = None
                            skipped += 1
                        else:
                            do_move = True

                if do_move and correction and correction != "INBOX":
                    result = call_text(await session.call_tool(
                        "move_message_to_folder",
                        {"message_id": msg["id"], "destination_folder": correction, "source_folder": "INBOX"},
                    ))
                    print(f"  ✓ {result}")
                    moved += 1

                    # Save the rule for this sender domain
                    rules["sender_rules"][domain] = correction
                    save_rules(rules)

            print("\n" + "=" * 70)
            print(f"Done. Moved: {moved}  |  Stayed in INBOX: {stayed}  |  Skipped: {skipped}")
            print(f"Claude API calls this run: {api_calls}  |  Fast (learned) lookups: {len(messages) - api_calls - skipped}")
            print(f"Total known sender rules saved: {len(rules['sender_rules'])}")


def main():
    parser = argparse.ArgumentParser(description="Smart email sorter")
    parser.add_argument("--auto",  action="store_true",
                        help="Auto mode: move without per-message confirmation")
    parser.add_argument("--count", type=int, default=20,
                        help="How many recent INBOX messages to process (default 20)")
    args = parser.parse_args()

    if args.auto:
        print("=== AUTO MODE — messages will be moved without confirmation ===")
        confirm = input("Are you sure? Type 'yes' to proceed: ").strip().lower()
        if confirm != "yes":
            print("Cancelled.")
            return
    else:
        print("=== TRAINING MODE — you will confirm each move ===")
        print("Commands at each prompt:")
        print("  y            — accept the proposed folder")
        print("  <folder>     — correct to a different folder (e.g. 'Newsletters')")
        print("  s            — skip this message (leave in INBOX, don't learn)")
        print(f"  Valid folders: {', '.join(TARGET_FOLDERS.keys())}\n")

    asyncio.run(run(args.count, args.auto))


if __name__ == "__main__":
    main()
