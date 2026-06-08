"""
Mail MCP Server (read-only)
===========================

A tiny MCP server exposing tools to look at your own IMAP mailbox
(e.g. a self-hosted Dovecot server) using Python's built-in imaplib.
No external API or third-party key needed — just your mail credentials.

This first version is READ-ONLY on purpose: it can list folders, list
recent messages, and read a message's contents — but it cannot move,
delete, or modify anything. That makes it safe to experiment with on a
real mailbox. A "move to spam" tool can be added later once you're
comfortable with how the agent behaves.

Set these environment variables before running (see README.md):
    export IMAP_HOST="mail.yourdomain.com"
    export IMAP_USER="you@yourdomain.com"
    export IMAP_PASSWORD="your-password-or-app-password"
    export IMAP_PORT="993"          # optional, defaults to 993 (IMAPS)

To run this server by itself (mostly for testing):
    python3 mail_mcp_server.py

Normally you won't run it directly — mail_agent.py starts it for you.
"""

import os
import sys
import imaplib
import email
from email.header import decode_header

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mail-server")

IMAP_HOST = os.environ.get("IMAP_HOST")
IMAP_USER = os.environ.get("IMAP_USER")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))


def _missing_config():
    if not (IMAP_HOST and IMAP_USER and IMAP_PASSWORD):
        return (
            "Error: IMAP is not configured. Please set IMAP_HOST, IMAP_USER, "
            "and IMAP_PASSWORD environment variables before starting the agent. "
            "See README.md for details."
        )
    return None


def _connect():
    """Open a fresh IMAP connection. We open/close per-call to keep this simple
    and avoid holding a stale connection open between tool calls."""
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(IMAP_USER, IMAP_PASSWORD)
    return conn


def _decode(value):
    """IMAP headers can be encoded (e.g. =?UTF-8?B?...?=) — decode to plain text."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = ""
    for text, charset in parts:
        if isinstance(text, bytes):
            decoded += text.decode(charset or "utf-8", errors="replace")
        else:
            decoded += text
    return decoded


@mcp.tool()
def list_mail_folders() -> str:
    """List the folders/mailboxes available in this mail account
    (e.g. INBOX, Sent, Junk, Spam, Drafts)."""
    error = _missing_config()
    if error:
        return error

    try:
        conn = _connect()
        status, folders = conn.list()
        conn.logout()
    except Exception as e:
        return f"Failed to connect to IMAP server: {e}"

    if status != "OK":
        return "Could not list folders."

    names = []
    for raw in folders:
        # Each entry looks like: b'(\\HasNoChildren) "/" "INBOX"'
        decoded = raw.decode("utf-8", errors="replace")
        name = decoded.split('"')[-2] if '"' in decoded else decoded
        names.append(name)

    return "Folders: " + ", ".join(names)


@mcp.tool()
def list_recent_messages(folder: str = "INBOX", count: int = 10) -> str:
    """List the most recent messages in a folder, with sender, subject, and date.

    Args:
        folder: The mailbox/folder to look in, e.g. "INBOX" or "Junk" (default "INBOX")
        count: How many recent messages to show, max 25 (default 10)
    """
    error = _missing_config()
    if error:
        return error

    count = max(1, min(count, 25))

    try:
        conn = _connect()
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            conn.logout()
            return f"Could not open folder '{folder}'. Try list_mail_folders to see valid names."

        status, data = conn.search(None, "ALL")
        if status != "OK":
            conn.logout()
            return f"Could not search folder '{folder}'."

        ids = data[0].split()
        recent_ids = ids[-count:][::-1]  # newest first

        lines = []
        for msg_id in recent_ids:
            status, msg_data = conn.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            if status != "OK":
                continue
            raw_headers = msg_data[0][1]
            msg = email.message_from_bytes(raw_headers)
            sender = _decode(msg.get("From", "Unknown sender"))
            subject = _decode(msg.get("Subject", "(no subject)"))
            date = msg.get("Date", "")
            lines.append(f"[id {msg_id.decode()}] From: {sender} | Subject: {subject} | Date: {date}")

        conn.logout()
    except Exception as e:
        return f"Failed to read messages: {e}"

    if not lines:
        return f"No messages found in '{folder}'."

    return f"Recent messages in '{folder}':\n" + "\n".join(lines)


@mcp.tool()
def read_message(message_id: str, folder: str = "INBOX") -> str:
    """Read the text content of a specific message by its ID
    (use list_recent_messages first to find an ID).

    Args:
        message_id: The numeric message ID shown by list_recent_messages
        folder: The mailbox/folder the message is in (default "INBOX")
    """
    error = _missing_config()
    if error:
        return error

    try:
        conn = _connect()
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            conn.logout()
            return f"Could not open folder '{folder}'."

        status, msg_data = conn.fetch(message_id.encode(), "(BODY.PEEK[])")
        conn.logout()
        if status != "OK" or not msg_data or msg_data[0] is None:
            return f"Could not find message {message_id} in '{folder}'."

        msg = email.message_from_bytes(msg_data[0][1])
        sender = _decode(msg.get("From", "Unknown sender"))
        subject = _decode(msg.get("Subject", "(no subject)"))

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    charset = part.get_content_charset() or "utf-8"
                    body = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
        else:
            charset = msg.get_content_charset() or "utf-8"
            body = msg.get_payload(decode=True).decode(charset, errors="replace")

        body = body.strip()
        if len(body) > 2000:
            body = body[:2000] + "\n... (truncated)"

        return f"From: {sender}\nSubject: {subject}\n\n{body}"
    except Exception as e:
        return f"Failed to read message: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
