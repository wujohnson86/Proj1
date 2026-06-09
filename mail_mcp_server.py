"""
Mail MCP Server
===============

A tiny MCP server exposing tools to look at (and, carefully, act on) your
own IMAP mailbox (e.g. a self-hosted Dovecot server) using Python's
built-in imaplib. No external API or third-party key needed — just your
mail credentials.

Most tools here are READ-ONLY: list folders, list recent messages, read a
message, and search by subject keyword. There is exactly ONE tool that
modifies your mailbox — move_message_to_folder — and it's designed to only
ever be called for a message the user has already seen and explicitly
confirmed (see spam_cleanup.py for a guided, confirm-before-acting flow).

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
import re
import sys
import imaplib
import email
from email.header import decode_header

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mail-server")

# Parses an IMAP LIST response line such as:
#   (\HasNoChildren) "/" "INBOX"
#   (\HasNoChildren) "." INBOX          <- name without quotes (some servers)
#   (\Noselect \HasChildren) "/" "Top/Sub Folder"
# Group 'name' captures everything after the flags and delimiter; quotes
# (if present) are stripped off afterwards in _folder_name_from_list_line.
_LIST_LINE_RE = re.compile(r'^\(([^)]*)\)\s+("[^"]*"|NIL)\s+(.+)$')


def _folder_name_from_list_line(raw_line) -> str:
    """Extract just the folder name from one raw LIST response entry."""
    decoded = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line
    match = _LIST_LINE_RE.match(decoded.strip())
    if not match:
        return decoded.strip()
    name = match.group(3).strip()
    if name.startswith('"') and name.endswith('"'):
        name = name[1:-1]
    return name

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
    and avoid holding a stale connection open between tool calls.

    Raises a RuntimeError with a detailed (but password-free) message if
    either the connection or the login step fails, so callers can surface
    something more useful than a bare 'authentication failed'.
    """
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as e:
        raise RuntimeError(
            f"Could not open a connection to {IMAP_HOST}:{IMAP_PORT} over IMAPS (SSL). "
            f"Underlying error: {type(e).__name__}: {e}. "
            f"Double-check IMAP_HOST and IMAP_PORT, and that the server accepts "
            f"IMAPS on that port (993 is the usual default)."
        )

    try:
        conn.login(IMAP_USER, IMAP_PASSWORD)
    except imaplib.IMAP4.error as e:
        # The server's actual response is the most useful debugging info here —
        # e.g. b'[AUTHENTICATIONFAILED] Authentication failed.' — and contains
        # no password, so it's safe to surface directly.
        try:
            conn.logout()
        except Exception:
            pass
        raise RuntimeError(
            f"Login to {IMAP_HOST}:{IMAP_PORT} as user '{IMAP_USER}' was rejected by the server. "
            f"Server said: {e}. "
            f"Double-check IMAP_USER and IMAP_PASSWORD (and whether this account needs "
            f"a username like 'johnson' vs the full email address 'johnson@snoopy.org')."
        )
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        raise RuntimeError(
            f"Unexpected error logging in to {IMAP_HOST}:{IMAP_PORT} as '{IMAP_USER}': "
            f"{type(e).__name__}: {e}"
        )

    return conn


@mcp.tool()
def test_connection() -> str:
    """Try connecting and logging in to the IMAP server, and report exactly
    what happens — useful for diagnosing 'authentication failed' problems
    without printing your password anywhere."""
    error = _missing_config()
    if error:
        return error

    report = [
        f"IMAP_HOST = {IMAP_HOST}",
        f"IMAP_PORT = {IMAP_PORT}",
        f"IMAP_USER = {IMAP_USER}",
        "IMAP_PASSWORD = (set, length hidden)" if IMAP_PASSWORD else "IMAP_PASSWORD = (NOT SET)",
        "",
    ]

    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        report.append(f"Connected to {IMAP_HOST}:{IMAP_PORT} over IMAPS — OK")
        report.append(f"Server greeting/capabilities: {conn.welcome}")
    except Exception as e:
        report.append(f"FAILED to connect: {type(e).__name__}: {e}")
        return "\n".join(report)

    try:
        status, resp = conn.login(IMAP_USER, IMAP_PASSWORD)
        report.append(f"Login status: {status}, server response: {resp}")
        report.append("Login succeeded!")
        conn.logout()
    except imaplib.IMAP4.error as e:
        report.append(f"Login REJECTED by server. Server said: {e}")
    except Exception as e:
        report.append(f"Unexpected error during login: {type(e).__name__}: {e}")

    return "\n".join(report)


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

    names = [_folder_name_from_list_line(raw) for raw in folders]

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


@mcp.tool()
def search_messages_by_subject(keyword: str, folder: str = "INBOX", count: int = 25) -> str:
    """Search a folder for messages whose subject contains a keyword (read-only).

    Args:
        keyword: Word or phrase to search for in the subject line, e.g. "viagra" or "winner"
        folder: The mailbox/folder to search in (default "INBOX")
        count: Maximum number of matches to show, max 50 (default 25)
    """
    error = _missing_config()
    if error:
        return error

    count = max(1, min(count, 50))

    try:
        conn = _connect()
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            conn.logout()
            return f"Could not open folder '{folder}'. Try list_mail_folders to see valid names."

        # IMAP SEARCH SUBJECT does a substring match on the decoded subject server-side.
        status, data = conn.search(None, "SUBJECT", f'"{keyword}"')
        if status != "OK":
            conn.logout()
            return f"Search failed in folder '{folder}'."

        ids = data[0].split()
        matched_ids = ids[-count:][::-1]  # newest first

        lines = []
        for msg_id in matched_ids:
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
        return f"Failed to search messages: {e}"

    if not lines:
        return f"No messages with '{keyword}' in the subject found in '{folder}'."

    return f"Messages in '{folder}' with '{keyword}' in the subject:\n" + "\n".join(lines)


@mcp.tool()
def move_message_to_folder(message_id: str, destination_folder: str, source_folder: str = "INBOX") -> str:
    """Move a single message from one folder to another (e.g. to SPAM).

    This DOES modify your mailbox — it copies the message to the destination
    folder and removes it from the source folder. Only call this for a
    message ID you have already shown the user and they have explicitly
    confirmed should be moved.

    Args:
        message_id: The numeric message ID (from list_recent_messages or search_messages_by_subject)
        destination_folder: The folder to move the message into, e.g. "SPAM"
        source_folder: The folder the message currently lives in (default "INBOX")
    """
    error = _missing_config()
    if error:
        return error

    try:
        conn = _connect()
        status, _ = conn.select(source_folder, readonly=False)
        if status != "OK":
            conn.logout()
            return f"Could not open folder '{source_folder}'."

        msg_id_bytes = message_id.encode()

        # Copy to destination, then mark the original as deleted and expunge it.
        status, _ = conn.copy(msg_id_bytes, destination_folder)
        if status != "OK":
            conn.logout()
            return (
                f"Could not copy message {message_id} to '{destination_folder}'. "
                f"Check the folder name with list_mail_folders."
            )

        conn.store(msg_id_bytes, "+FLAGS", "\\Deleted")
        conn.expunge()
        conn.logout()
    except Exception as e:
        return f"Failed to move message: {e}"

    return f"Moved message {message_id} from '{source_folder}' to '{destination_folder}'."


@mcp.tool()
def folder_exists(folder_name: str) -> str:
    """Check whether a mailbox/folder with this exact name already exists.

    Args:
        folder_name: The folder name to check, e.g. "SPAM"
    """
    error = _missing_config()
    if error:
        return error

    try:
        conn = _connect()
        status, folders = conn.list()
        conn.logout()
    except Exception as e:
        return f"Failed to check folders: {e}"

    if status != "OK":
        return "Could not list folders to check."

    names = [_folder_name_from_list_line(raw) for raw in folders]

    if folder_name in names:
        return f"Yes — '{folder_name}' already exists."
    return f"No — '{folder_name}' does not exist yet. Existing folders: {', '.join(names)}"


@mcp.tool()
def create_mail_folder(folder_name: str) -> str:
    """Create a new mailbox/folder (e.g. SPAM) if it doesn't already exist.

    This DOES modify your mailbox (it adds a new folder). Only call this
    after the user has explicitly confirmed they want it created.

    Args:
        folder_name: The name of the folder to create, e.g. "SPAM"
    """
    error = _missing_config()
    if error:
        return error

    try:
        conn = _connect()

        status, folders = conn.list()
        existing = [_folder_name_from_list_line(raw) for raw in folders]

        if folder_name in existing:
            conn.logout()
            return f"'{folder_name}' already exists — nothing to do."

        status, response = conn.create(folder_name)
        conn.logout()
    except Exception as e:
        return f"Failed to create folder '{folder_name}': {e}"

    if status != "OK":
        return f"Server refused to create '{folder_name}': {response}"

    return f"Created new folder '{folder_name}'."


@mcp.tool()
def peek_message(message_id: str, folder: str = "INBOX", chars: int = 400) -> str:
    """Fetch just the first N characters of a message's body plus its
    From/Subject headers — cheaper than read_message for classification
    purposes where you don't need the full content.

    Args:
        message_id: The numeric message ID (from list_recent_messages)
        folder: The mailbox/folder the message is in (default "INBOX")
        chars: How many characters of body to return, max 800 (default 400)
    """
    error = _missing_config()
    if error:
        return error

    chars = max(50, min(chars, 800))

    try:
        conn = _connect()
        status, _ = conn.select(folder, readonly=True)
        if status != "OK":
            conn.logout()
            return f"Could not open folder '{folder}'."

        status, msg_data = conn.fetch(message_id.encode(), "(BODY.PEEK[])")
        conn.logout()
        if status != "OK" or not msg_data or msg_data[0] is None:
            return f"Could not fetch message {message_id}."

        msg = email.message_from_bytes(msg_data[0][1])
        sender = _decode(msg.get("From", "Unknown"))
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

        snippet = body.strip()[:chars]
        return f"From: {sender}\nSubject: {subject}\nSnippet: {snippet}"
    except Exception as e:
        return f"Failed to peek at message: {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
