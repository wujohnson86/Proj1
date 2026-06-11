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

Rules:
- Be decisive and specific. Nearly every email fits a non-INBOX category.
- Marketing/deals/sales from any company → Promotions (even from stores you like)
- Any order/shipping/invoice → Receipts
- Blog posts, digests, tips, how-tos → Newsletters
- Recruiters, job offers, freelance inquiries → Consulting
- Bank/credit card/billing → Finance
- LinkedIn/Twitter/social platform pings → Social
- Flight/hotel/booking confirmations → Travel
- Personal email from a real human → Friends
- ONLY use INBOX for genuinely important, personal, or action-required email
  that doesn't fit any category above. Do NOT default to INBOX out of caution."""


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

            # Queue of (message_id, destination_folder, display_line) to execute later.
            # Built up during review; flushed when user types GO or at end of session.
            move_queue = []

            async def flush_queue():
                """Execute all queued moves and clear the queue."""
                nonlocal moved
                if not move_queue:
                    print("  (nothing in queue)")
                    return
                print(f"\n  Executing {len(move_queue)} queued move(s)...")
                for q_id, q_dest, q_display in move_queue:
                    r = call_text(await session.call_tool(
                        "move_message_to_folder",
                        {"message_id": q_id, "destination_folder": q_dest, "source_folder": "INBOX"},
                    ))
                    print(f"    ✓ {r}")
                    moved += 1
                move_queue.clear()
                print(f"  Queue flushed.\n")

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

                # FOLDER_CHOICES is mutable — new folders added via 'n' appear
                # in the picker for the rest of this session.
                FOLDER_CHOICES = [f for f in TARGET_FOLDERS if f != "INBOX"]

                def pick_folder_interactively() -> str | None:
                    print()
                    for n, name in enumerate(FOLDER_CHOICES, 1):
                        print(f"    {n}. {name:15s} — {TARGET_FOLDERS[name][:55]}")
                    print(f"    0. Cancel (keep in INBOX)")
                    raw = input("  Pick a number: ").strip()
                    if raw == "0" or raw == "":
                        return None
                    if raw.isdigit() and 1 <= int(raw) <= len(FOLDER_CHOICES):
                        return FOLDER_CHOICES[int(raw) - 1]
                    print("  Invalid number — keeping in INBOX.")
                    return None

                async def create_new_folder_interactively() -> str | None:
                    """Ask for a name, create the folder if needed, return its name."""
                    name = input("  New folder name: ").strip()
                    if not name:
                        return None
                    # Create if it doesn't exist (tool handles the "already exists" case)
                    result = call_text(await session.call_tool(
                        "create_mail_folder", {"folder_name": name}
                    ))
                    print(f"  {result}")
                    # Register it so the picker and rules know about it
                    if name not in TARGET_FOLDERS:
                        TARGET_FOLDERS[name] = f"Custom folder: {name}"
                        FOLDER_CHOICES.append(name)
                    return name

                if auto:
                    do_move = folder != "INBOX"
                    correction = folder
                    if do_move:
                        # Auto mode executes immediately — no queue
                        r = call_text(await session.call_tool(
                            "move_message_to_folder",
                            {"message_id": msg["id"], "destination_folder": correction,
                             "source_folder": "INBOX"},
                        ))
                        print(f"  ✓ {r}")
                        moved += 1
                        rules["sender_rules"][domain] = correction
                        save_rules(rules)
                    continue
                else:
                    if folder == "INBOX":
                        prompt = "  Proposed: leave in INBOX. Move somewhere? [Enter=keep / f=list / n=new folder / s=skip]: "
                    else:
                        prompt = f"  Move to '{folder}'? [y / f=list / n=new folder / Enter=keep in INBOX / s=skip]: "

                    raw = input(prompt).strip().lower()

                    if raw == "f":
                        picked = pick_folder_interactively()
                        raw = picked if picked else ""

                    elif raw == "n":
                        picked = await create_new_folder_interactively()
                        raw = picked if picked else ""

                    if raw == "s":
                        do_move = False
                        correction = None
                        skipped += 1
                    elif raw in ("y", "") and folder != "INBOX":
                        do_move = True
                        correction = folder
                    elif raw == "" and folder == "INBOX":
                        do_move = False
                        correction = "INBOX"
                    else:
                        correction = raw.strip()
                        if correction not in TARGET_FOLDERS:
                            print(f"  Unknown folder '{correction}'. Type 'f' to list or 'n' to create.")
                            do_move = False
                            correction = None
                            skipped += 1
                        elif correction == "INBOX":
                            do_move = False
                        else:
                            do_move = True

                if do_move and correction and correction != "INBOX":
                    # Queue the move rather than executing it immediately.
                    move_queue.append((msg["id"], correction, msg["raw"]))
                    print(f"  → queued: move to '{correction}'  "
                          f"[{len(move_queue)} in queue — type GO to execute now]")
                    rules["sender_rules"][domain] = correction
                    save_rules(rules)

                    # --- Offer to bulk-queue all other messages from same sender ---
                    bulk = input(
                        f"  Also queue ALL other '{domain}' messages → '{correction}'? [y/N]: "
                    ).strip().lower()
                    if bulk == "y":
                        others_text = call_text(await session.call_tool(
                            "search_messages_by_sender",
                            {"sender_pattern": domain, "folder": "INBOX", "count": 100},
                        ))
                        others = parse_listing(others_text)
                        others = [m for m in others if m["id"] != msg["id"]]
                        if not others:
                            print(f"  No other '{domain}' messages found in INBOX.")
                        else:
                            print(f"  Found {len(others)} more — showing first 5:")
                            for o in others[:5]:
                                print(f"    {o['raw'][:90]}")
                            if len(others) > 5:
                                print(f"    ... and {len(others) - 5} more")
                            confirm_bulk = input(
                                f"  Queue all {len(others)} → '{correction}'? [y/N]: "
                            ).strip().lower()
                            if confirm_bulk == "y":
                                for o in others:
                                    move_queue.append((o["id"], correction, o["raw"]))
                                print(f"  → {len(others)} added to queue  "
                                      f"[{len(move_queue)} total in queue]")
                else:
                    stayed += 1
                    if correction == "INBOX" and domain not in rules["sender_rules"]:
                        rules["sender_rules"][domain] = "INBOX"
                        save_rules(rules)

                # Let the user flush the queue mid-session by typing GO at any prompt.
                # We check after each message so they can bail out of reviewing early too.
                if move_queue:
                    go = input(
                        f"  [Queue: {len(move_queue)} pending] "
                        f"Type GO to execute now, or Enter to keep reviewing: "
                    ).strip().upper()
                    if go == "GO":
                        await flush_queue()

            # Flush any remaining queue at end of review
            if move_queue:
                print(f"\n{'=' * 70}")
                print(f"Review done. {len(move_queue)} move(s) still queued:")
                for q_id, q_dest, q_display in move_queue:
                    print(f"  → {q_display[:80]}  →  {q_dest}")
                go = input("\nExecute all queued moves now? [y/N]: ").strip().lower()
                if go == "y":
                    await flush_queue()
                else:
                    print(f"Skipped — {len(move_queue)} moves NOT executed. "
                          f"Rules were already saved so they'll be auto-sorted next run.")
                    move_queue.clear()

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
        print("  y            — queue move to proposed folder")
        print("  Enter        — keep it in INBOX (no move)")
        print("  Newsletters  — (or any folder name) override the proposal")
        print("  f            — show numbered folder list to pick from")
        print("  n            — create a brand new folder and move there")
        print("  s            — skip entirely, don't learn this sender")
        print("  GO           — execute all queued moves immediately and keep going")
        print()
        print("Moves are QUEUED (not executed immediately) — at the end you")
        print("get one final prompt to execute them all, or type GO mid-session.")
        print()
        print("After queuing a move you'll also be asked:")
        print("  Queue ALL other messages from the same sender? [y/N]")
        print(f"\n  Valid folders: {', '.join(f for f in TARGET_FOLDERS if f != 'INBOX')}\n")

    asyncio.run(run(args.count, args.auto))


if __name__ == "__main__":
    main()
