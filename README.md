# MCP Agent Playground (Beginner Project)

The main thing to run is **`agent.py`** — a single agent that connects to
all three MCP servers below at once and figures out on its own which
tool(s) to use based on what you ask (it can even combine them, e.g.
"weather in Tokyo and the price of Bitcoin").

Small Python pairs of (MCP server + Claude agent), each demonstrating the
same pattern with a different free API — these also still work standalone
if you want to see one in isolation:

- `weather_mcp_server.py` / `weather_agent.py` — weather lookups via
  OpenWeatherMap (needs a free API key).
- `crypto_mcp_server.py` / `crypto_agent.py` — cryptocurrency prices via
  CoinGecko (no API key needed at all).
- `news_mcp_server.py` / `news_agent.py` — news headlines via NewsAPI.org
  (needs a free API key).
- `mail_mcp_server.py` / `mail_agent.py` — browse your own IMAP mailbox
  (e.g. self-hosted Dovecot): list folders, list recent messages, read a
  message, search by subject keyword. No third-party API or key — just
  your mail login.
- `spam_cleanup.py` — a guided (non-chat) tool that searches a folder for
  spam-like subjects and, only after you explicitly confirm exactly which
  messages, moves them to your SPAM folder. See "Spam cleanup" below.

Each "agent" file is the one you run — it automatically launches its
matching "server" file(s) as a subprocess and talks to them over MCP.

## 1. Get your API keys ready

- Anthropic API key: https://console.anthropic.com/
- OpenWeatherMap API key (free tier is fine, only needed for the weather agent): https://openweathermap.org/api
- NewsAPI key (free tier is fine, only needed for the news agent): https://newsapi.org/register
- (No key needed for the crypto agent — CoinGecko's basic API is open.)

## 2. Install dependencies

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 3. Set your keys as environment variables

Never put keys directly in the code. Export them in your shell instead:

```bash
export ANTHROPIC_API_KEY="your-anthropic-key-here"
export OPENWEATHER_API_KEY="your-openweather-key-here"
export NEWSAPI_KEY="your-newsapi-key-here"

# For the mail agent (your own IMAP/Dovecot server):
export IMAP_HOST="mail.yourdomain.com"
export IMAP_USER="you@yourdomain.com"
export IMAP_PASSWORD="your-mail-password"
export IMAP_PORT="993"   # optional, defaults to 993 (IMAPS)
```

(You only need to set the keys/values for the agents you plan to run — the
crypto agent doesn't need any key besides `ANTHROPIC_API_KEY`, and the mail
agent only needs the `IMAP_*` variables, not the weather/news keys.)

**About the mail agent:** it talks to your real mailbox over IMAPS (port
993, encrypted). `mail_agent.py` is a free-chat agent for *looking around*
(folders, recent messages, reading, searching by subject) — Claude decides
which read-only tool to call based on what you ask. The one tool that can
actually change your mailbox (`move_message_to_folder`) is *not* something
this chat agent will use on its own — see "Spam cleanup" below for the
guided, confirm-first way to actually move messages.

(Add these two lines to your `~/.bashrc` if you don't want to re-type them
every time you open a terminal.)

## 4. Run an agent

The easiest way to start is the all-in-one agent — it has access to every
tool and figures out which one(s) to use:

```bash
python3 agent.py
```

Try asking it things like:
- "What's the weather in Tokyo?"
- "What's the price of Bitcoin?"
- "What's the top tech news today?"
- "Give me the weather in Paris AND the price of Ethereum" (it can chain
  multiple tool calls to answer one question!)

You can also run any of the single-purpose agents on their own:

```bash
python3 weather_agent.py
# or
python3 crypto_agent.py
# or
python3 news_agent.py
# or
python3 mail_agent.py
```

Each one automatically starts its matching MCP server behind the scenes —
you don't need to run the servers separately.

Try asking the weather agent things like:
- "What's the weather in Tokyo?"
- "Is it warmer in Paris or Berlin right now?"

Try asking the crypto agent things like:
- "What's the price of Bitcoin?"
- "Compare Ethereum and Dogecoin in EUR"

Try asking the news agent things like:
- "What's the top news today?"
- "Summarize the latest technology headlines"

Try asking the mail agent things like:
- "What folders do I have?"
- "Show me my 5 most recent emails in the inbox"
- "What's in the latest message from <someone>?"

Type `quit` to exit any of them.

## Spam cleanup (guided, confirm-first)

`spam_cleanup.py` is a different kind of tool: instead of free chat, it
walks you through a fixed sequence of steps and never moves anything
without your explicit "yes". It does NOT use the Anthropic API at all —
just the MCP mail server directly — because this workflow doesn't need an
LLM improvising on a real, hard-to-reverse mailbox action.

```bash
python3 spam_cleanup.py
```

What happens, step by step:
1. It lists your IMAP folders
2. You choose which folder to scan (press Enter for INBOX)
3. You choose the destination folder (press Enter for `SPAM`) — if it
   doesn't already exist, it asks whether to create it before continuing
   (it won't create anything, or scan anything, without your "y")
4. You type one or more subject keywords to search for, comma-separated
   (e.g. `viagra, you've won, free money`)
5. It shows you every matching message (sender / subject / date) with a number
6. You type the numbers of the ones to move (or `all`, or nothing to cancel)
7. It shows you exactly what it's about to do and asks you to type `yes`
8. Only then does it move those specific messages to the destination folder

Nothing is moved until you've seen the exact list and typed `yes` twice
(once to pick, once to confirm).

## How it fits together

```
You  -->  weather_agent.py  -->  Claude (Anthropic API)
                |                       |
                |  (decides to call a tool)
                v
          MCP connection
                |
                v
       weather_mcp_server.py  -->  OpenWeatherMap API
```
