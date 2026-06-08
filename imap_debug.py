"""
IMAP Connection Debugger
========================

A standalone script — no MCP, no Claude, no agent — that just tries to
connect and log in to your IMAP server and prints exactly what happens at
each step. Use this first whenever mail_agent.py / spam_cleanup.py report
"authentication failed" — it'll usually pinpoint whether the problem is
the host/port, the username format, or the password/server-side auth.

Setup:
    export IMAP_HOST="ec2.snoopy.org"
    export IMAP_USER="johnson"
    export IMAP_PASSWORD="your-password"
    export IMAP_PORT="993"     # optional, defaults to 993

Run:
    python3 imap_debug.py
"""

import os
import sys
import imaplib

IMAP_HOST = os.environ.get("IMAP_HOST")
IMAP_USER = os.environ.get("IMAP_USER")
IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))


def main():
    print("=== IMAP connection debug ===")
    print(f"IMAP_HOST     = {IMAP_HOST!r}")
    print(f"IMAP_PORT     = {IMAP_PORT!r}")
    print(f"IMAP_USER     = {IMAP_USER!r}")
    print(f"IMAP_PASSWORD = {'(set, ' + str(len(IMAP_PASSWORD)) + ' chars)' if IMAP_PASSWORD else '(NOT SET)'}")
    print()

    if not all([IMAP_HOST, IMAP_USER, IMAP_PASSWORD]):
        sys.exit("One or more of IMAP_HOST / IMAP_USER / IMAP_PASSWORD is not set. "
                 "Export them first (see the top of this file).")

    # Step 1: TCP + TLS connection
    print(f"Step 1: connecting to {IMAP_HOST}:{IMAP_PORT} over IMAPS (SSL)...")
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        print()
        print("This means the script couldn't even establish an encrypted connection.")
        print("Things to check:")
        print("  - Is IMAP_HOST correct and resolvable (try: ping " + str(IMAP_HOST) + ")")
        print("  - Is IMAP_PORT correct? 993 is standard for IMAPS. If Dovecot is")
        print("    configured for plain IMAP + STARTTLS instead, it may be on 143.")
        print("  - Is a firewall blocking the connection from this machine?")
        sys.exit(1)

    print("  OK — connected.")
    print(f"  Server greeting: {conn.welcome!r}")
    try:
        typ, caps = conn.capability()
        print(f"  Server capabilities: {caps}")
    except Exception:
        pass
    print()

    # Step 2: login
    print(f"Step 2: logging in as {IMAP_USER!r}...")
    try:
        status, response = conn.login(IMAP_USER, IMAP_PASSWORD)
        print(f"  OK — login succeeded. Server response: {response}")
    except imaplib.IMAP4.error as e:
        print(f"  REJECTED by server: {e}")
        print()
        print("This is the server actively refusing your credentials. Things to check:")
        print("  - Is the password correct? (try logging in with a regular mail client")
        print("    using the exact same host/port/username/password to rule out a typo)")
        print("  - Does Dovecot expect the FULL email address as the username")
        print("    (e.g. 'johnson@snoopy.org') rather than just 'johnson'? This is a")
        print("    very common cause of 'AUTHENTICATIONFAILED' on hosted IMAP servers.")
        print("  - Check the Dovecot server's own logs (often /var/log/mail.log or")
        print("    journalctl -u dovecot) for the specific rejection reason.")
        sys.exit(1)
    except Exception as e:
        print(f"  Unexpected error: {type(e).__name__}: {e}")
        sys.exit(1)

    # Step 3: list folders, just to prove we're really in
    print()
    print("Step 3: listing folders to confirm full access...")
    try:
        status, folders = conn.list()
        for raw in folders:
            print(f"  {raw.decode('utf-8', errors='replace')}")
    except Exception as e:
        print(f"  Could not list folders: {type(e).__name__}: {e}")

    conn.logout()
    print()
    print("=== All good — your IMAP credentials and connection work! ===")
    print("If mail_agent.py / spam_cleanup.py still fail, the problem is likely")
    print("in how those scripts are launching the MCP server subprocess (e.g. the")
    print("environment variables aren't reaching it) rather than the IMAP setup itself.")


if __name__ == "__main__":
    main()
